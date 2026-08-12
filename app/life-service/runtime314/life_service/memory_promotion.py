"""Deterministic P15 promotion evidence math (M3).

No probabilities.  Support uses integer noisy-or folding per lineage root
(I11/I12): records sharing any lineage root collapse into one independence
group, and only the maximum weight inside a group counts.  Re-summarizing the
same event therefore never inflates independence.  All thresholds and hashes
are integer-only and replay identically on Windows and Linux.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from contracts import (
    MemoryDerivationV1,
    MemoryPromotionDisposition,
    derive_promotion_key,
)


BASE_EVIDENCE_WEIGHT_MILLI = {
    "execution_verified": 1000,
    "migration_verified": 1000,
    "observed": 1000,
    "user_asserted": 750,
    "model_inference": 0,
    "reflection": 0,
    "prospective": 0,
}

L2_MAX_PARENTS = 64
L3_MIN_SUPPORT_MILLI = 650
L3_MAX_COUNTER_MILLI = 350
L3_MIN_INDEPENDENCE_GROUPS = 2
L3_MIN_RECURRENCE = 2
L3_DIRECT_GROUPS_MIN = 1
L3_DIRECT_CAUSAL_UTILITY_MILLI = 700
L5_MIN_SUPPORT_MILLI = 850
L5_MAX_COUNTER_MILLI = 150
L5_MIN_INDEPENDENCE_GROUPS = 3
L5_DIRECT_GROUPS_MIN = 1


def noisy_or(weights: tuple[int, ...]) -> int:
    """Integer noisy-or: S(n+1) = S(n) + w - floor(S(n) * w / 1000)."""

    support = 0
    for weight in weights:
        if isinstance(weight, bool) or not isinstance(weight, int):
            raise ValueError("evidence weight must be an integer")
        if weight < 0:
            raise ValueError("evidence weight cannot be negative")
        support = support + weight - (support * weight) // 1000
    return support


def net_support(support_milli: int, counter_milli: int) -> int:
    if support_milli < 0 or counter_milli < 0:
        raise ValueError("support and counter must be non-negative")
    return max(0, support_milli - counter_milli)


def lineage_root_sha256(lineage_root_event_ids: tuple[str, ...]) -> str:
    return "|".join(sorted(set(lineage_root_event_ids)))


@dataclass(frozen=True, slots=True)
class EvidenceGroup:
    lineage_root_sha256: str
    member_derivation_ids: tuple[str, ...]
    weight_milli: int
    direct: bool


def fold_independence(
    derivations: tuple[MemoryDerivationV1, ...],
    weights: Mapping[str, int],
) -> tuple[EvidenceGroup, ...]:
    """Fold derivations that share ANY lineage root into one group (I11).

    Two derivations are in the same independence group whenever their root
    sets intersect, so re-summarizing an event never creates a second group
    even when the summary adds new derived roots.
    """

    derivations = tuple(derivations)
    count = len(derivations)
    root_sets = tuple(
        set(item.lineage_root_event_ids) for item in derivations
    )
    parent = list(range(count))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(first: int, second: int) -> None:
        root_first = find(first)
        root_second = find(second)
        if root_first != root_second:
            parent[root_second] = root_first

    for first in range(count):
        for second in range(first + 1, count):
            if root_sets[first] & root_sets[second]:
                union(first, second)
    buckets: dict[int, list[MemoryDerivationV1]] = {}
    for index, derivation in enumerate(derivations):
        buckets.setdefault(find(index), []).append(derivation)
    groups: list[EvidenceGroup] = []
    for members in buckets.values():
        members = tuple(members)
        union_roots = tuple(
            sorted(
                {
                    root
                    for member in members
                    for root in member.lineage_root_event_ids
                }
            )
        )
        member_weights = tuple(
            weights.get(item.derivation_id, 0) for item in members
        )
        groups.append(
            EvidenceGroup(
                lineage_root_sha256="|".join(union_roots),
                member_derivation_ids=tuple(
                    item.derivation_id for item in members
                ),
                weight_milli=max(member_weights),
                direct=any(weight >= 1000 for weight in member_weights),
            )
        )
    return tuple(
        sorted(groups, key=lambda group: group.lineage_root_sha256)
    )


def _lineage_roots(
    derivations: tuple[MemoryDerivationV1, ...],
) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                root
                for derivation in derivations
                for root in derivation.lineage_root_event_ids
            }
        )
    )


def _disposition(
    *,
    life_id: str,
    principal_ref: str,
    target_layer: str,
    claim_key: str,
    semantic_domain: str,
    policy_version: str,
    parent_derivations: tuple[MemoryDerivationV1, ...],
    lineage_root_event_ids: tuple[str, ...],
    allowed: bool,
    reason_codes: tuple[str, ...],
    support_milli: int,
    counter_milli: int,
    independence_group_count: int,
    recurrence_count: int,
    valid_from_ms: int,
    created_at_ms: int,
) -> MemoryPromotionDisposition:
    promotion_key = derive_promotion_key(
        policy_version=policy_version,
        life_id=life_id,
        target_layer=target_layer,
        parent_assertion_sha256=tuple(
            sorted(
                {
                    item.memory_assertion_sha256
                    for item in parent_derivations
                }
            )
        ),
        semantic_domain=semantic_domain,
        claim_key=claim_key,
        lineage_root_event_ids=lineage_root_event_ids,
    )
    return MemoryPromotionDisposition(
        promotion_key=promotion_key,
        life_id=life_id,
        principal_ref=principal_ref,
        target_layer=target_layer,
        claim_key=claim_key,
        semantic_domain=semantic_domain,
        policy_version=policy_version,
        parent_assertion_sha256=tuple(
            sorted(
                {
                    item.memory_assertion_sha256
                    for item in parent_derivations
                }
            )
        ),
        lineage_root_event_ids=lineage_root_event_ids,
        allowed=allowed,
        reason_codes=tuple(sorted(set(reason_codes))),
        support_milli=support_milli,
        counter_milli=counter_milli,
        independence_group_count=independence_group_count,
        recurrence_count=recurrence_count,
        valid_from_ms=valid_from_ms,
        created_at_ms=created_at_ms,
        disposition_sha256="0" * 64,
    ).with_computed_disposition_sha256()


def evaluate_l2(
    *,
    l1_derivations: tuple[MemoryDerivationV1, ...],
    life_id: str,
    principal_ref: str,
    claim_key: str,
    semantic_domain: str,
    policy_version: str,
    valid_from_ms: int,
    created_at_ms: int,
    episode_boundary: bool = True,
) -> MemoryPromotionDisposition:
    """L1 -> L2 diary aggregation (compression, not fact upgrade)."""

    reason_codes: list[str] = []
    allowed = (
        episode_boundary
        and 1 <= len(l1_derivations) <= L2_MAX_PARENTS
        and all(
            item.layer == "L1_STREAM" for item in l1_derivations
        )
    )
    if not episode_boundary:
        reason_codes.append("no_episode_boundary")
    if not 1 <= len(l1_derivations) <= L2_MAX_PARENTS:
        reason_codes.append("l1_aggregate_size_out_of_range")
    if allowed:
        reason_codes.append("l2_episode_aggregation")
    return _disposition(
        life_id=life_id,
        principal_ref=principal_ref,
        target_layer="L2_DIARY",
        claim_key=claim_key,
        semantic_domain=semantic_domain,
        policy_version=policy_version,
        parent_derivations=l1_derivations,
        lineage_root_event_ids=_lineage_roots(l1_derivations),
        allowed=allowed,
        reason_codes=tuple(reason_codes),
        support_milli=1000,
        counter_milli=0,
        independence_group_count=len(
            fold_independence(
                l1_derivations,
                {item.derivation_id: 1000 for item in l1_derivations},
            )
        ),
        recurrence_count=0,
        valid_from_ms=valid_from_ms,
        created_at_ms=created_at_ms,
    )


def evaluate_l3(
    *,
    l2_derivations: tuple[MemoryDerivationV1, ...],
    support_weights: Mapping[str, int],
    counter_weights: Mapping[str, int],
    causal_utility_milli: Mapping[str, int],
    recurrence_count: int,
    life_id: str,
    principal_ref: str,
    claim_key: str,
    semantic_domain: str,
    policy_version: str,
    valid_from_ms: int,
    created_at_ms: int,
) -> MemoryPromotionDisposition:
    """L2 -> L3 experience promotion with the P15 thresholds."""

    if not l2_derivations:
        raise ValueError("L3 promotion requires at least one L2 derivation")
    groups = fold_independence(l2_derivations, support_weights)
    support = noisy_or(tuple(group.weight_milli for group in groups))
    counter = noisy_or(
        tuple(counter_weights.get(item.derivation_id, 0) for item in l2_derivations)
    )
    independence_groups = len(groups)
    direct_groups = sum(1 for group in groups if group.direct)
    max_causal = max(
        causal_utility_milli.get(item.derivation_id, 0)
        for item in l2_derivations
    )
    condition_a = (
        independence_groups >= L3_MIN_INDEPENDENCE_GROUPS
        and recurrence_count >= L3_MIN_RECURRENCE
    )
    condition_b = (
        direct_groups >= L3_DIRECT_GROUPS_MIN
        and max_causal >= L3_DIRECT_CAUSAL_UTILITY_MILLI
    )
    reason_codes: list[str] = []
    if support < L3_MIN_SUPPORT_MILLI:
        reason_codes.append("insufficient_support")
    if counter > L3_MAX_COUNTER_MILLI:
        reason_codes.append("counter_too_high")
    if not condition_a:
        reason_codes.append("not_enough_independent_groups_or_recurrence")
    if not condition_b:
        reason_codes.append("not_enough_direct_verified_causal_utility")
    allowed = (
        support >= L3_MIN_SUPPORT_MILLI
        and counter <= L3_MAX_COUNTER_MILLI
        and (condition_a or condition_b)
    )
    if allowed:
        reason_codes.append("l3_support_threshold")
    return _disposition(
        life_id=life_id,
        principal_ref=principal_ref,
        target_layer="L3_EXPERIENCE",
        claim_key=claim_key,
        semantic_domain=semantic_domain,
        policy_version=policy_version,
        parent_derivations=l2_derivations,
        lineage_root_event_ids=_lineage_roots(l2_derivations),
        allowed=allowed,
        reason_codes=tuple(reason_codes),
        support_milli=support,
        counter_milli=counter,
        independence_group_count=independence_groups,
        recurrence_count=recurrence_count,
        valid_from_ms=valid_from_ms,
        created_at_ms=created_at_ms,
    )


def evaluate_l5(
    *,
    candidates: tuple[MemoryDerivationV1, ...],
    support_weights: Mapping[str, int],
    counter_weights: Mapping[str, int],
    recurrence_count: int,
    life_id: str,
    principal_ref: str,
    claim_key: str,
    semantic_domain: str,
    policy_version: str,
    valid_from_ms: int,
    created_at_ms: int,
) -> MemoryPromotionDisposition:
    """L3/L4 -> L5 core promotion (A stability / B reconfirm / C fusion)."""

    if not candidates:
        raise ValueError("L5 promotion requires at least one candidate")
    groups = fold_independence(candidates, support_weights)
    support = noisy_or(tuple(group.weight_milli for group in groups))
    counter = noisy_or(
        tuple(
            counter_weights.get(item.derivation_id, 0)
            for item in candidates
        )
    )
    independence_groups = len(groups)
    direct_groups = sum(1 for group in groups if group.direct)
    explicit_count = sum(
        1
        for item in candidates
        if item.origin == "USER_EXPLICIT" and item.layer == "L4_EXPLICIT"
    )
    l3_count = sum(1 for item in candidates if item.layer == "L3_EXPERIENCE")
    has_temporary_expiry = any(
        item.expires_at_ms is not None for item in candidates
    )
    stable_l3 = any(
        item.layer == "L3_EXPERIENCE"
        for item in candidates
    )
    reason_codes: list[str] = []
    if support < L5_MIN_SUPPORT_MILLI:
        reason_codes.append("insufficient_support")
    if counter > L5_MAX_COUNTER_MILLI:
        reason_codes.append("counter_too_high")
    if independence_groups < L5_MIN_INDEPENDENCE_GROUPS:
        reason_codes.append("not_enough_independent_groups")
    if direct_groups < L5_DIRECT_GROUPS_MIN:
        reason_codes.append("no_direct_evidence_group")
    if has_temporary_expiry:
        reason_codes.append("temporary_expiry_blocks_l5")

    path_a = (
        independence_groups >= L5_MIN_INDEPENDENCE_GROUPS
        and support >= L5_MIN_SUPPORT_MILLI
        and counter <= L5_MAX_COUNTER_MILLI
        and direct_groups >= L5_DIRECT_GROUPS_MIN
        and not has_temporary_expiry
    )
    path_b = (
        explicit_count >= 2
        and not has_temporary_expiry
        and counter <= L5_MAX_COUNTER_MILLI
    )
    path_c = (
        l3_count >= 1
        and explicit_count >= 1
        and support >= L5_MIN_SUPPORT_MILLI
        and counter <= L5_MAX_COUNTER_MILLI
        and not has_temporary_expiry
    )
    allowed = path_a or path_b or path_c
    if path_a:
        reason_codes.append("l5_stability")
    if path_b:
        reason_codes.append("l5_reconfirm")
    if path_c:
        reason_codes.append("l5_fusion")
    return _disposition(
        life_id=life_id,
        principal_ref=principal_ref,
        target_layer="L5_CORE",
        claim_key=claim_key,
        semantic_domain=semantic_domain,
        policy_version=policy_version,
        parent_derivations=candidates,
        lineage_root_event_ids=_lineage_roots(candidates),
        allowed=allowed,
        reason_codes=tuple(reason_codes),
        support_milli=support,
        counter_milli=counter,
        independence_group_count=independence_groups,
        recurrence_count=recurrence_count,
        valid_from_ms=valid_from_ms,
        created_at_ms=created_at_ms,
    )


__all__ = [
    "BASE_EVIDENCE_WEIGHT_MILLI",
    "EvidenceGroup",
    "evaluate_l2",
    "evaluate_l3",
    "evaluate_l5",
    "fold_independence",
    "lineage_root_sha256",
    "net_support",
    "noisy_or",
]
