"""Deterministic retrieval and context projection for stable cognition.

Retrieval never grants authority. It re-evaluates persisted evidence at read time
so stale or no-longer-supported cognition is not projected merely because an old
statement once reached STABLE/CORE.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Sequence

from contracts.cognition_statement import CognitionStatement

from .stability import StabilityPolicy, evaluate_evidence, highest_eligible_level
from .store import WorldCognitionStore


_LEVEL_ORDER = {"C0": 0, "C1": 1, "C2": 2, "C3": 3, "C4": 4}


@dataclass(frozen=True, slots=True)
class RetrievedCognition:
    statement: CognitionStatement
    live_confidence_milli: int
    support_group_count: int
    lexical_score: int


def _value_text(statement: CognitionStatement) -> str:
    value = statement.value
    if value.kind == "entity_ref":
        return str(value.entity_ref or "")
    if value.kind == "string":
        return str(value.string_value or "")
    if value.kind == "integer":
        return str(value.integer_value)
    return "true" if bool(value.boolean_value) else "false"


def _terms(text: str) -> tuple[str, ...]:
    # Preserve CJK runs while tokenizing ASCII identifiers and dotted paths.
    return tuple(
        dict.fromkeys(
            token.casefold()
            for token in re.findall(r"[A-Za-z0-9_.:@-]+|[\u3400-\u9fff]+", str(text or ""))
            if token.strip()
        )
    )


def _lexical_score(statement: CognitionStatement, query_terms: Sequence[str]) -> int:
    if not query_terms:
        return 0
    fields = (
        statement.subject_ref.casefold(),
        statement.predicate.casefold(),
        _value_text(statement).casefold(),
        statement.claim_kind.casefold(),
    )
    score = 0
    for term in query_terms:
        if any(term == field for field in fields):
            score += 12
        elif any(term in field for field in fields):
            score += 7
        elif any(field and field in term for field in fields):
            score += 3
    return score


class CognitionRetriever:
    def __init__(self, store: WorldCognitionStore, *, policy: StabilityPolicy | None = None) -> None:
        self.store = store
        self.policy = policy or StabilityPolicy()

    def retrieve(
        self,
        *,
        life_id: str,
        domain: str,
        world_scope_hash: str,
        principal_scope_hash: str,
        now_ms: int,
        query: str = "",
        allowed_privacy_scopes: Sequence[str] = ("system",),
        max_items: int = 8,
    ) -> tuple[RetrievedCognition, ...]:
        if max_items <= 0:
            return ()
        query_terms = _terms(query)
        heads = self.store.list_active_heads(
            life_id=life_id,
            domain=domain,
            world_scope_hash=world_scope_hash,
            principal_scope_hash=principal_scope_hash,
            statuses=("STABLE", "CORE"),
            limit=max(100, max_items * 20),
        )
        allowed_privacy = set(allowed_privacy_scopes)
        retrieved: list[RetrievedCognition] = []
        for statement in heads:
            if statement.privacy_scope not in allowed_privacy:
                continue
            support = self.store.get_evidence_many(statement.supporting_evidence_ids)
            counter = self.store.get_evidence_many(statement.counterevidence_ids)
            if len(support) != len(statement.supporting_evidence_ids) or len(counter) != len(statement.counterevidence_ids):
                # Broken evidence closure means the cognition cannot be projected.
                continue
            report = evaluate_evidence(
                cognition_id=statement.cognition_id,
                life_id=statement.life_id,
                domain=statement.domain,
                world_scope_hash=statement.world_scope_hash,
                principal_scope_hash=statement.principal_scope_hash,
                support=support,
                counter=counter,
                now_ms=now_ms,
                policy=self.policy,
            )
            eligible = highest_eligible_level(report, self.policy)
            required = "C3" if statement.stability_level == "C4" else statement.stability_level
            if _LEVEL_ORDER[eligible] < _LEVEL_ORDER[required]:
                continue
            retrieved.append(
                RetrievedCognition(
                    statement=statement,
                    live_confidence_milli=report.net_milli,
                    support_group_count=report.support_group_count,
                    lexical_score=_lexical_score(statement, query_terms),
                )
            )
        retrieved.sort(
            key=lambda item: (
                item.lexical_score,
                _LEVEL_ORDER[item.statement.stability_level],
                item.live_confidence_milli,
                item.statement.cognition_id,
            ),
            reverse=True,
        )
        return tuple(retrieved[:max_items])

    def project_context(
        self,
        *,
        life_id: str,
        domain: str,
        world_scope_hash: str,
        principal_scope_hash: str,
        now_ms: int,
        query: str = "",
        allowed_privacy_scopes: Sequence[str] = ("system",),
        max_items: int = 8,
        max_chars: int = 6000,
    ) -> str:
        if max_chars <= 0:
            return ""
        items = self.retrieve(
            life_id=life_id,
            domain=domain,
            world_scope_hash=world_scope_hash,
            principal_scope_hash=principal_scope_hash,
            now_ms=now_ms,
            query=query,
            allowed_privacy_scopes=allowed_privacy_scopes,
            max_items=max_items,
        )
        if not items:
            return ""
        lines = [
            "[World Cognition — evidence-backed context only]",
            "这些是当前仍被证据支持的稳定认知；它们不授予权限。若当前直接观察冲突，应重新验证现实。",
        ]
        for item in items:
            statement = item.statement
            line = (
                f"- {statement.subject_ref} | {statement.predicate} | {_value_text(statement)} "
                f"[{statement.status}/{statement.stability_level}; evidence={item.live_confidence_milli}/1000; "
                f"independent_groups={item.support_group_count}]"
            )
            candidate = "\n".join((*lines, line))
            if len(candidate) > max_chars:
                break
            lines.append(line)
        return "\n".join(lines) if len(lines) > 2 else ""


__all__ = ["CognitionRetriever", "RetrievedCognition"]
