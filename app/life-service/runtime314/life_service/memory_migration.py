"""Deterministic legacy-memory migration into protected causal-memory projections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from contracts import (
    CausalNodeV3,
    MemoryAssertionV3,
    MemoryRelationV3,
    canonical_json_bytes,
    canonical_sha256,
)

from .store import LifeShadowStore


@dataclass(frozen=True, slots=True)
class LegacyMemoryRecord:
    legacy_memory_id: str
    memory_type: str
    status: str
    content: Mapping[str, object]
    search_terms: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LegacyMemoryRelation:
    source_legacy_memory_id: str
    target_legacy_memory_id: str
    relation_label: str


@dataclass(frozen=True, slots=True)
class LegacyMemoryMigrationReport:
    life_id: str
    assertion_count: int
    causal_node_count: int
    ordinary_relation_count: int
    causal_hypothesis_count: int
    legacy_to_memory_id: tuple[tuple[str, str], ...]
    report_sha256: str

    def has_valid_report_sha256(self) -> bool:
        return self.report_sha256 == canonical_sha256(
            {
                "assertion_count": self.assertion_count,
                "causal_hypothesis_count": self.causal_hypothesis_count,
                "causal_node_count": self.causal_node_count,
                "legacy_to_memory_id": self.legacy_to_memory_id,
                "life_id": self.life_id,
                "ordinary_relation_count": self.ordinary_relation_count,
            }
        )


def _assertion_kind(memory_type: str) -> str:
    return {
        "preference": "user_preference",
        "user_preference": "user_preference",
        "constraint": "hard_constraint",
        "hard_constraint": "hard_constraint",
        "goal": "goal",
        "relationship": "relationship",
        "skill": "skill",
        "fact": "observation",
        "observation": "observation",
    }.get(memory_type.strip().casefold(), "legacy")


def _retention_class(assertion_kind: str) -> str:
    if assertion_kind in {"hard_constraint", "goal"}:
        return "CHECKPOINT"
    if assertion_kind in {"user_preference", "relationship", "skill"}:
        return "LONG_TERM_MEMORY"
    return "ACTIVE_WORKING"


def migrate_legacy_memory_records(
    store: LifeShadowStore,
    *,
    life_id: str,
    records: tuple[LegacyMemoryRecord, ...],
    relations: tuple[LegacyMemoryRelation, ...] = (),
    migrated_at_ms: int,
    privacy_scope: str,
) -> LegacyMemoryMigrationReport:
    if not life_id or not privacy_scope or migrated_at_ms < 0:
        raise ValueError("legacy memory migration boundary is invalid")
    legacy_ids = tuple(record.legacy_memory_id for record in records)
    if (
        not records
        or any(not value or len(value) > 256 for value in legacy_ids)
        or legacy_ids != tuple(sorted(set(legacy_ids)))
    ):
        raise ValueError("legacy memory records must be sorted and uniquely identified")
    mapping = {
        legacy_id: "mem_"
        + canonical_sha256(
            {
                "domain": "tiangong.life.legacy-memory.v1",
                "legacy_memory_id": legacy_id,
                "life_id": life_id,
            }
        )
        for legacy_id in legacy_ids
    }
    for ordinal, record in enumerate(records):
        content = canonical_json_bytes(dict(record.content))
        existing = store.get_latest_memory_assertion(mapping[record.legacy_memory_id])
        if existing is None:
            protected = store.put_protected_payload(
                content,
                life_id=life_id,
                privacy_scope=privacy_scope,
                created_at_ms=migrated_at_ms + ordinal,
            )
            record_created_at_ms = migrated_at_ms + ordinal
        else:
            if (
                existing.revision != 1
                or existing.protected_payload_id is None
                or store.read_protected_payload(existing.protected_payload_id) != content
            ):
                raise ValueError("legacy memory retry disagrees with migrated content")
            protected = store.get_protected_payload(existing.protected_payload_id)
            assert protected is not None
            record_created_at_ms = existing.created_at_ms
        kind = _assertion_kind(record.memory_type)
        active = record.status.strip().casefold() == "active"
        assertion = MemoryAssertionV3(
            memory_id=mapping[record.legacy_memory_id],
            life_id=life_id,
            revision=1,
            supersedes_assertion_sha256=None,
            assertion_kind=kind,
            epistemic_status="observed",
            lifecycle_status="active" if active else "recall_suppressed",
            protected_payload_id=protected.payload_id,
            protected_payload_sha256=protected.ciphertext_sha256,
            deletion_tombstone_id=None,
            privacy_scope=privacy_scope,
            retention_class=_retention_class(kind),
            source_event_ids=(),
            causal_hypothesis_ids=(),
            causal_utility_milli=0,
            user_importance_milli=500 if kind in {"hard_constraint", "goal"} else 0,
            verification_strength_milli=500,
            recurrence_count=0,
            future_dependency_milli=500 if kind in {"hard_constraint", "goal"} else 0,
            privacy_cost_milli=500,
            contradiction_penalty_milli=0,
            staleness_milli=0,
            valid_from_ms=migrated_at_ms,
            expires_at_ms=None,
            created_at_ms=record_created_at_ms,
            assertion_sha256="0" * 64,
        ).with_computed_assertion_sha256()
        store.put_memory_assertion(assertion, search_terms=record.search_terms)
        node = CausalNodeV3(
            node_id="cnd_"
            + canonical_sha256(
                {
                    "domain": "tiangong.life.legacy-causal-node.v1",
                    "memory_id": assertion.memory_id,
                }
            ),
            life_id=life_id,
            node_kind=(
                "goal"
                if kind == "goal"
                else "constraint"
                if kind == "hard_constraint"
                else "memory_assertion"
            ),
            source_ref=assertion.memory_id,
            protected_payload_id=protected.payload_id,
            protected_payload_sha256=protected.ciphertext_sha256,
            privacy_scope=privacy_scope,
            retention_class=assertion.retention_class,
            recall_status=assertion.lifecycle_status,
            source_event_ids=(),
            created_at_ms=record_created_at_ms,
            node_sha256="0" * 64,
        ).with_computed_node_sha256()
        store.put_causal_node(node, search_terms=record.search_terms)

    allowed_relations = {
        "supports",
        "related_to",
        "contradicts",
        "refines",
        "derived_from",
        "temporal_before",
    }
    ordered_relations = tuple(
        sorted(
            relations,
            key=lambda value: (
                value.source_legacy_memory_id,
                value.target_legacy_memory_id,
                value.relation_label,
            ),
        )
    )
    if len(set(ordered_relations)) != len(ordered_relations):
        raise ValueError("legacy memory relations contain duplicates")
    for ordinal, relation in enumerate(ordered_relations):
        if (
            relation.source_legacy_memory_id not in mapping
            or relation.target_legacy_memory_id not in mapping
            or not relation.relation_label
            or len(relation.relation_label) > 256
        ):
            raise ValueError("legacy memory relation is invalid")
        normalized = relation.relation_label.strip().casefold()
        relation_kind = normalized if normalized in allowed_relations else "legacy_unclassified"
        original_label = None if relation_kind != "legacy_unclassified" else relation.relation_label
        value = MemoryRelationV3(
            relation_id="mrl_"
            + canonical_sha256(
                {
                    "domain": "tiangong.life.legacy-memory-relation.v1",
                    "label": relation.relation_label,
                    "source": mapping[relation.source_legacy_memory_id],
                    "target": mapping[relation.target_legacy_memory_id],
                }
            ),
            life_id=life_id,
            source_memory_id=mapping[relation.source_legacy_memory_id],
            relation_kind=relation_kind,
            original_relation_label=original_label,
            target_ref=mapping[relation.target_legacy_memory_id],
            evidence_class="observed",
            supporting_event_ids=(),
            created_at_ms=migrated_at_ms + len(records) + ordinal,
            relation_sha256="0" * 64,
        ).with_computed_relation_sha256()
        store.put_memory_relation(value)

    values = {
        "assertion_count": len(records),
        "causal_hypothesis_count": 0,
        "causal_node_count": len(records),
        "legacy_to_memory_id": tuple(sorted(mapping.items())),
        "life_id": life_id,
        "ordinary_relation_count": len(ordered_relations),
    }
    return LegacyMemoryMigrationReport(
        life_id=life_id,
        assertion_count=len(records),
        causal_node_count=len(records),
        ordinary_relation_count=len(ordered_relations),
        causal_hypothesis_count=0,
        legacy_to_memory_id=tuple(sorted(mapping.items())),
        report_sha256=canonical_sha256(values),
    )


__all__ = [
    "LegacyMemoryMigrationReport",
    "LegacyMemoryRecord",
    "LegacyMemoryRelation",
    "migrate_legacy_memory_records",
]
