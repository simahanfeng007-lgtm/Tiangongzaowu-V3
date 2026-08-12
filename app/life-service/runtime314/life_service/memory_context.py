"""P15 M5: layered context selection, lineage dedupe, privacy and injection.

Context keeps conversation/run continuity mandatory and layers memories on
top of it (L1..L5) with weighted priority.  One lineage contributes at most
one representative; invalidated, expired, foreign-principal or mismatched-
privacy derivations are excluded; external or injection-marked content is
rendered as DATA/EVIDENCE and never as a system instruction.
"""

from __future__ import annotations

from dataclasses import dataclass

from contracts import MemoryAssertionV3, MemoryDerivationV1, canonical_sha256
from .store import LifeShadowStore


LAYER_BONUS_MILLI = {
    "L5_CORE": 2_500,
    "L4_EXPLICIT": 2_200,
    "L3_EXPERIENCE": 1_400,
    "L2_DIARY": 700,
    "L1_STREAM": 300,
}

INSTRUCTION_AUTHORITY_DOMAINS = frozenset(
    {"OPERATING_RULE", "USER_PREFERENCE"}
)
_INJECTION_MARKERS = (
    "ignore previous instructions",
    "ignore previous prompt",
    "ignore previous system",
    "system prompt",
    "忽略系统提示",
    "忽略此前指令",
    "忽略之前指令",
    "你是chatgpt",
    "你必须",
)


@dataclass(frozen=True, slots=True)
class LayeredMemoryItem:
    memory_id: str
    derivation_id: str
    layer: str
    semantic_domain: str
    claim_key: str
    lineage_root_sha256: str
    summary: str
    priority_milli: int
    section: str
    item_sha256: str


def layer_bonus_milli(layer: str) -> int:
    if layer not in LAYER_BONUS_MILLI:
        raise ValueError("memory layer is invalid")
    return LAYER_BONUS_MILLI[layer]


def lineage_root_sha256(lineage_root_event_ids: tuple[str, ...]) -> str:
    return "|".join(sorted(set(lineage_root_event_ids)))


def context_priority_milli(
    derivation: MemoryDerivationV1,
    assertion: MemoryAssertionV3,
    *,
    now_ms: int,
) -> int:
    """Deterministic layer-weighted priority (not a probability)."""

    score = (
        layer_bonus_milli(derivation.layer)
        + assertion.user_importance_milli
        + assertion.verification_strength_milli
        + min(1000, assertion.future_dependency_milli)
        - assertion.privacy_cost_milli
        - assertion.contradiction_penalty_milli
        - assertion.staleness_milli
    )
    if (
        derivation.expires_at_ms is not None
        and derivation.expires_at_ms < now_ms
    ):
        score -= 10_000
    return max(-5_000, min(10_000, score))


def dedupe_lineage(
    items: tuple[LayeredMemoryItem, ...],
) -> tuple[LayeredMemoryItem, ...]:
    """One representative per connected lineage component (I18).

    Two items belong to the same lineage whenever their root sets intersect,
    so a refined summary that inherits a parent root never duplicates the
    parent into Context.
    """

    items = tuple(items)
    count = len(items)
    root_sets = tuple(
        set(item.lineage_root_sha256.split("|")) for item in items
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
    buckets: dict[int, list[LayeredMemoryItem]] = {}
    for index, item in enumerate(items):
        buckets.setdefault(find(index), []).append(item)
    best: list[LayeredMemoryItem] = []
    for members in buckets.values():
        representative = max(
            members,
            key=lambda value: (
                layer_bonus_milli(value.layer),
                value.priority_milli,
                value.memory_id,
            ),
        )
        best.append(representative)
    return tuple(
        sorted(best, key=lambda value: (-value.priority_milli, value.memory_id))
    )


def is_injection_marked(summary: str) -> bool:
    lowered = str(summary or "").casefold()
    return any(marker in lowered for marker in _INJECTION_MARKERS)


def classify_instruction_authority(
    derivation: MemoryDerivationV1,
    assertion: MemoryAssertionV3,
    summary: str,
) -> str:
    """INSTRUCTION only for rule/preference authority; else DATA/EVIDENCE."""

    if is_injection_marked(summary):
        return "EVIDENCE"
    if (
        derivation.semantic_domain in INSTRUCTION_AUTHORITY_DOMAINS
        and derivation.origin in {"USER_EXPLICIT", "PROMOTION", "MIGRATION"}
        and assertion.epistemic_status in {"user_asserted", "verified"}
    ):
        return "INSTRUCTION"
    return "DATA"


def render_context_sections(
    *,
    instruction_items: tuple[LayeredMemoryItem, ...],
    data_items: tuple[LayeredMemoryItem, ...],
    evidence_items: tuple[LayeredMemoryItem, ...],
    max_chars: int = 8_000,
) -> dict[str, str]:
    """Render bounded context sections; injection evidence is prefixed."""

    if max_chars <= 0:
        raise ValueError("context section budget must be positive")

    def render(items: tuple[LayeredMemoryItem, ...], prefix: str) -> str:
        parts: list[str] = []
        used = 0
        for item in items:
            line = f"{prefix}{item.summary}"
            if used + len(line) > max_chars:
                break
            parts.append(line)
            used += len(line)
        return "\n".join(parts)

    return {
        "instruction": render(instruction_items, "- "),
        "data": render(data_items, "- "),
        "evidence": render(
            evidence_items, "[EVIDENCE-ONLY] - "
        ),
    }


def select_layered_memories(
    store: LifeShadowStore,
    *,
    life_id: str,
    principal_ref: str,
    privacy_scope: str,
    now_ms: int,
    limit: int = 64,
) -> tuple[
    tuple[LayeredMemoryItem, ...],
    tuple[LayeredMemoryItem, ...],
    tuple[LayeredMemoryItem, ...],
    int,
]:
    """Select active, in-scope, non-expired layered memories for Context."""

    if not 1 <= limit <= 4096:
        raise ValueError("layered context limit is invalid")
    derivations = store.list_memory_derivations(
        life_id=life_id, active_only=True, limit=4096
    )
    instruction: list[LayeredMemoryItem] = []
    data: list[LayeredMemoryItem] = []
    evidence: list[LayeredMemoryItem] = []
    skipped = 0
    for derivation in derivations:
        if derivation.principal_ref != principal_ref:
            skipped += 1
            continue
        if derivation.privacy_scope != privacy_scope:
            skipped += 1
            continue
        if (
            derivation.expires_at_ms is not None
            and derivation.expires_at_ms < now_ms
        ):
            skipped += 1
            continue
        assertion = store.get_memory_assertion(
            derivation.memory_id, derivation.memory_revision
        )
        if assertion is None or assertion.protected_payload_id is None:
            skipped += 1
            continue
        try:
            plaintext = store.read_protected_payload(
                assertion.protected_payload_id
            ).decode("utf-8", errors="strict")
        except Exception:
            skipped += 1
            continue
        summary = plaintext[:2_000]
        section = classify_instruction_authority(
            derivation, assertion, summary
        )
        item = LayeredMemoryItem(
            memory_id=derivation.memory_id,
            derivation_id=derivation.derivation_id,
            layer=derivation.layer,
            semantic_domain=derivation.semantic_domain,
            claim_key=derivation.claim_key,
            lineage_root_sha256=lineage_root_sha256(
                derivation.lineage_root_event_ids
            ),
            summary=summary,
            priority_milli=context_priority_milli(
                derivation, assertion, now_ms=now_ms
            ),
            section=section,
            item_sha256=canonical_sha256(
                {
                    "domain": "tiangong.life.layered-context-item.v1",
                    "derivation_id": derivation.derivation_id,
                    "summary_sha256": canonical_sha256(summary),
                    "priority_milli": context_priority_milli(
                        derivation, assertion, now_ms=now_ms
                    ),
                }
            ),
        )
        bucket = (
            instruction if section == "INSTRUCTION"
            else evidence if section == "EVIDENCE"
            else data
        )
        bucket.append(item)
    selected = dedupe_lineage(
        tuple(instruction + data + evidence)
    )[:limit]
    instruction_selected = tuple(
        item for item in selected if item.section == "INSTRUCTION"
    )
    data_selected = tuple(
        item for item in selected if item.section == "DATA"
    )
    evidence_selected = tuple(
        item for item in selected if item.section == "EVIDENCE"
    )
    return instruction_selected, data_selected, evidence_selected, skipped


__all__ = [
    "INSTRUCTION_AUTHORITY_DOMAINS",
    "LAYER_BONUS_MILLI",
    "LayeredMemoryItem",
    "classify_instruction_authority",
    "context_priority_milli",
    "dedupe_lineage",
    "is_injection_marked",
    "layer_bonus_milli",
    "lineage_root_sha256",
    "render_context_sections",
    "select_layered_memories",
]
