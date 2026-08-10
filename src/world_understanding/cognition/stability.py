"""Deterministic evidence aggregation and cognition stability policy.

`confidence_milli` is an evidence-support margin, not a calibrated probability.
Integer arithmetic keeps decisions stable across Windows/Linux and Python builds.
Evidence independence is re-derived from provenance connectivity: a caller cannot
create a new quorum merely by assigning different independence-group hashes to
items that share a lineage root.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable, Mapping

from contracts.canonical import canonical_sha256
from contracts.cognition_evidence import CognitionEvidence


@dataclass(frozen=True, slots=True)
class StabilityPolicy:
    schema: str = "tiangong.world_cognition.stability.v1"
    same_source_gamma_milli: int = 250
    max_clock_skew_ms: int = 5 * 60 * 1000
    provisional_threshold_milli: int = 300
    stable_threshold_milli: int = 600
    core_threshold_milli: int = 850
    stable_max_counter_milli: int = 350
    core_max_counter_milli: int = 150

    def as_dict(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "same_source_gamma_milli": self.same_source_gamma_milli,
            "max_clock_skew_ms": self.max_clock_skew_ms,
            "provisional_threshold_milli": self.provisional_threshold_milli,
            "stable_threshold_milli": self.stable_threshold_milli,
            "core_threshold_milli": self.core_threshold_milli,
            "stable_max_counter_milli": self.stable_max_counter_milli,
            "core_max_counter_milli": self.core_max_counter_milli,
            "class_factor_milli": dict(CLASS_FACTOR_MILLI),
            "source_factor_milli": dict(SOURCE_FACTOR_MILLI),
            "half_life_ms": dict(HALF_LIFE_MS),
            "effective_independence": "declared-group-or-shared-lineage-connected-component",
        }

    @property
    def sha256(self) -> str:
        return canonical_sha256(self.as_dict())


CLASS_FACTOR_MILLI: Mapping[str, int] = {
    "observed": 1000,
    "execution_verified": 1000,
    "migration_verified": 1000,
    "user_asserted": 700,
    "model_inference": 0,
    "reflection": 0,
    "prospective": 0,
}

SOURCE_FACTOR_MILLI: Mapping[str, int] = {
    "fact_execution": 1000,
    "code_perception": 950,
    "system_authority": 1000,
    "migration": 1000,
    "user_instruction": 750,
    "memory": 600,
    "model_synthesis": 0,
}

HALF_LIFE_MS: Mapping[str, int | None] = {
    "transient": 5 * 60 * 1000,
    "short": 6 * 60 * 60 * 1000,
    "medium": 3 * 24 * 60 * 60 * 1000,
    "long": 30 * 24 * 60 * 60 * 1000,
    "structural": None,
}

DIRECT_CLASSES = frozenset({"observed", "execution_verified", "migration_verified"})
DIRECT_SOURCES = frozenset({"fact_execution", "code_perception", "system_authority", "migration"})


@dataclass(frozen=True, slots=True)
class _WeightedEvidence:
    evidence_id: str
    declared_group: str
    lineage_roots: tuple[str, ...]
    group: str
    weight_milli: int
    pre_fresh_weight_milli: int
    direct: bool


@dataclass(frozen=True, slots=True)
class StabilityReport:
    support_milli: int
    counter_milli: int
    net_milli: int
    support_groups: tuple[str, ...]
    counter_groups: tuple[str, ...]
    direct_support_groups: tuple[str, ...]
    conflicted_groups: tuple[str, ...]
    dropped_self_derived: tuple[str, ...]
    dropped_expired: tuple[str, ...]
    dropped_not_yet_valid: tuple[str, ...]
    dropped_invalid: tuple[str, ...]
    zero_authority: tuple[str, ...]
    correlation_discount_milli: int
    staleness_penalty_milli: int

    @property
    def support_group_count(self) -> int:
        return len(self.support_groups)

    @property
    def counter_group_count(self) -> int:
        return len(self.counter_groups)

    @property
    def direct_support_group_count(self) -> int:
        return len(self.direct_support_groups)


def _milli_mul(*values: int) -> int:
    result = 1000
    for value in values:
        result = (result * max(0, min(1000, int(value)))) // 1000
    return max(0, min(1000, result))


def _freshness_milli(evidence: CognitionEvidence, now_ms: int) -> int:
    half_life = HALF_LIFE_MS.get(evidence.volatility_class)
    if half_life is None:
        return 1000
    age = max(0, now_ms - evidence.observed_at_ms)
    return max(0, min(1000, (1000 * half_life) // (half_life + age)))


def _weight_one(
    evidence: CognitionEvidence,
    *,
    cognition_id: str,
    life_id: str,
    domain: str,
    world_scope_hash: str,
    principal_scope_hash: str,
    now_ms: int,
    policy: StabilityPolicy,
) -> tuple[_WeightedEvidence | None, str | None]:
    if not evidence.has_valid_evidence_sha256():
        return None, "invalid"
    if (
        evidence.life_id != life_id
        or evidence.domain != domain
        or evidence.world_scope_hash != world_scope_hash
        or evidence.principal_scope_hash != principal_scope_hash
    ):
        return None, "invalid"
    if cognition_id in evidence.ancestor_cognition_ids:
        return None, "self"
    if evidence.valid_from_ms > now_ms:
        return None, "not_yet_valid"
    if evidence.valid_until_ms is not None and evidence.valid_until_ms < now_ms:
        return None, "expired"
    if evidence.observed_at_ms > now_ms + policy.max_clock_skew_ms:
        return None, "invalid"

    authority = min(
        evidence.source_credibility_milli,
        evidence.authority_ceiling_milli,
        evidence.provenance_integrity_milli,
    )
    class_factor = CLASS_FACTOR_MILLI.get(evidence.evidence_class, 0)
    source_factor = SOURCE_FACTOR_MILLI.get(evidence.source_ref.source_kind, 0)
    coverage_factor = evidence.coverage_milli if evidence.observation_mode in {"negative", "aggregate"} else 1000
    pre_fresh = _milli_mul(authority, class_factor, source_factor, coverage_factor)
    final = _milli_mul(pre_fresh, _freshness_milli(evidence, now_ms))
    direct = (
        evidence.evidence_class in DIRECT_CLASSES
        and evidence.source_ref.source_kind in DIRECT_SOURCES
        and final > 0
    )
    return _WeightedEvidence(
        evidence_id=evidence.evidence_id,
        declared_group=evidence.independence_group_hash,
        lineage_roots=tuple(evidence.lineage_root_hashes),
        group=evidence.independence_group_hash,
        weight_milli=final,
        pre_fresh_weight_milli=pre_fresh,
        direct=direct,
    ), None if final > 0 else "zero"


def _normalize_effective_groups(
    support: list[_WeightedEvidence],
    counter: list[_WeightedEvidence],
) -> tuple[list[_WeightedEvidence], list[_WeightedEvidence]]:
    """Collapse evidence connected by declared group OR any shared lineage root."""
    combined = [*support, *counter]
    if not combined:
        return support, counter
    parent = list(range(len(combined)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        a, b = find(left), find(right)
        if a != b:
            if a > b:
                a, b = b, a
            parent[b] = a

    owner: dict[str, int] = {}
    for index, item in enumerate(combined):
        tokens = (f"g:{item.declared_group}", *(f"r:{root}" for root in item.lineage_roots))
        for token in tokens:
            previous = owner.get(token)
            if previous is None:
                owner[token] = index
            else:
                union(index, previous)

    members: dict[int, list[int]] = {}
    for index in range(len(combined)):
        members.setdefault(find(index), []).append(index)

    effective: dict[int, str] = {}
    for root, indices in members.items():
        declared_groups = sorted({combined[index].declared_group for index in indices})
        lineage_roots = sorted({lineage for index in indices for lineage in combined[index].lineage_roots})
        key = canonical_sha256(
            {
                "domain": "tiangong.cognition.effective-independence.v1",
                "declared_groups": declared_groups,
                "lineage_roots": lineage_roots,
            }
        )
        for index in indices:
            effective[index] = key

    normalized = [replace(item, group=effective[index]) for index, item in enumerate(combined)]
    return normalized[: len(support)], normalized[len(support) :]


def _noisy_or(values: Iterable[int]) -> int:
    combined = 0
    for value in values:
        value = max(0, min(1000, int(value)))
        combined = combined + value - (combined * value) // 1000
        combined = min(1000, combined)
    return combined


def _group_scores(
    weighted: Iterable[_WeightedEvidence],
    *,
    gamma_milli: int,
    use_pre_fresh: bool = False,
) -> tuple[dict[str, int], set[str]]:
    grouped: dict[str, list[_WeightedEvidence]] = {}
    for item in weighted:
        grouped.setdefault(item.group, []).append(item)
    scores: dict[str, int] = {}
    direct_groups: set[str] = set()
    for group, items in grouped.items():
        ordered = sorted(
            items,
            key=lambda item: (
                item.pre_fresh_weight_milli if use_pre_fresh else item.weight_milli,
                item.evidence_id,
            ),
            reverse=True,
        )
        factor = 1000
        total = 0
        for item in ordered:
            weight = item.pre_fresh_weight_milli if use_pre_fresh else item.weight_milli
            total += (weight * factor) // 1000
            factor = (factor * gamma_milli) // 1000
        scores[group] = min(1000, total)
        if any(item.direct for item in items):
            direct_groups.add(group)
    return scores, direct_groups


def evaluate_evidence(
    *,
    cognition_id: str,
    life_id: str,
    domain: str,
    world_scope_hash: str,
    principal_scope_hash: str,
    support: Iterable[CognitionEvidence],
    counter: Iterable[CognitionEvidence] = (),
    now_ms: int,
    policy: StabilityPolicy | None = None,
) -> StabilityReport:
    policy = policy or StabilityPolicy()
    support_items: list[_WeightedEvidence] = []
    counter_items: list[_WeightedEvidence] = []
    dropped: dict[str, set[str]] = {
        "self": set(), "expired": set(), "not_yet_valid": set(), "invalid": set(), "zero": set()
    }

    def collect(source: Iterable[CognitionEvidence], target: list[_WeightedEvidence]) -> None:
        seen_ids: set[str] = set()
        for evidence in source:
            if evidence.evidence_id in seen_ids:
                continue
            seen_ids.add(evidence.evidence_id)
            item, reason = _weight_one(
                evidence,
                cognition_id=cognition_id,
                life_id=life_id,
                domain=domain,
                world_scope_hash=world_scope_hash,
                principal_scope_hash=principal_scope_hash,
                now_ms=now_ms,
                policy=policy,
            )
            if item is not None and item.weight_milli > 0:
                target.append(item)
            elif reason is not None:
                dropped[reason].add(evidence.evidence_id)

    collect(support, support_items)
    collect(counter, counter_items)
    support_items, counter_items = _normalize_effective_groups(support_items, counter_items)

    support_group_names = {item.group for item in support_items}
    counter_group_names = {item.group for item in counter_items}
    conflicted = support_group_names & counter_group_names
    if conflicted:
        support_items = [item for item in support_items if item.group not in conflicted]
        counter_items = [item for item in counter_items if item.group not in conflicted]

    support_scores, direct_groups = _group_scores(support_items, gamma_milli=policy.same_source_gamma_milli)
    counter_scores, _ = _group_scores(counter_items, gamma_milli=policy.same_source_gamma_milli)
    support_pre_fresh, _ = _group_scores(support_items, gamma_milli=policy.same_source_gamma_milli, use_pre_fresh=True)
    counter_pre_fresh, _ = _group_scores(counter_items, gamma_milli=policy.same_source_gamma_milli, use_pre_fresh=True)

    support_total = _noisy_or(support_scores[group] for group in sorted(support_scores))
    counter_total = _noisy_or(counter_scores[group] for group in sorted(counter_scores))
    support_ungrouped = _noisy_or(item.weight_milli for item in sorted(support_items, key=lambda item: item.evidence_id))
    counter_ungrouped = _noisy_or(item.weight_milli for item in sorted(counter_items, key=lambda item: item.evidence_id))
    support_no_stale = _noisy_or(support_pre_fresh[group] for group in sorted(support_pre_fresh))
    counter_no_stale = _noisy_or(counter_pre_fresh[group] for group in sorted(counter_pre_fresh))

    correlation_discount = min(
        1000,
        max(0, support_ungrouped - support_total) + max(0, counter_ungrouped - counter_total),
    )
    staleness_penalty = min(
        1000,
        max(0, support_no_stale - support_total) + max(0, counter_no_stale - counter_total),
    )

    return StabilityReport(
        support_milli=support_total,
        counter_milli=counter_total,
        net_milli=max(0, support_total - counter_total),
        support_groups=tuple(sorted(group for group, score in support_scores.items() if score > 0)),
        counter_groups=tuple(sorted(group for group, score in counter_scores.items() if score > 0)),
        direct_support_groups=tuple(sorted(group for group in direct_groups if support_scores.get(group, 0) > 0)),
        conflicted_groups=tuple(sorted(conflicted)),
        dropped_self_derived=tuple(sorted(dropped["self"])),
        dropped_expired=tuple(sorted(dropped["expired"])),
        dropped_not_yet_valid=tuple(sorted(dropped["not_yet_valid"])),
        dropped_invalid=tuple(sorted(dropped["invalid"])),
        zero_authority=tuple(sorted(dropped["zero"])),
        correlation_discount_milli=correlation_discount,
        staleness_penalty_milli=staleness_penalty,
    )


def highest_eligible_level(report: StabilityReport, policy: StabilityPolicy | None = None) -> str:
    policy = policy or StabilityPolicy()
    if (
        report.net_milli >= policy.core_threshold_milli
        and report.support_group_count >= 3
        and report.direct_support_group_count >= 1
        and report.counter_milli <= policy.core_max_counter_milli
    ):
        return "C3"
    if (
        report.net_milli >= policy.stable_threshold_milli
        and report.support_group_count >= 2
        and report.direct_support_group_count >= 1
        and report.counter_milli <= policy.stable_max_counter_milli
    ):
        return "C2"
    if report.net_milli >= policy.provisional_threshold_milli and report.support_group_count >= 1:
        return "C1"
    return "C0"


def challenge_is_material(report: StabilityReport, *, current_level: str) -> bool:
    thresholds = {
        "C0": (300, 1), "C1": (350, 1), "C2": (500, 1), "C3": (650, 2), "C4": (750, 2)
    }
    threshold, groups = thresholds.get(current_level, (1000, 99))
    if report.counter_milli >= 900 and report.counter_group_count >= 1:
        return True
    return report.counter_milli >= threshold and report.counter_group_count >= groups


__all__ = [
    "CLASS_FACTOR_MILLI", "DIRECT_CLASSES", "DIRECT_SOURCES", "HALF_LIFE_MS",
    "SOURCE_FACTOR_MILLI", "StabilityPolicy", "StabilityReport",
    "challenge_is_material", "evaluate_evidence", "highest_eligible_level",
]
