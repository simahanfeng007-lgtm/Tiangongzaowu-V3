"""Bounded repository-context enrichment from the committed Software World graph.

No filesystem, Git, network, model, Gateway, scheduler, or mutable repository
index is consulted here. The function only ranks records that are already in
the committed Software World graph and delegates traversal to the M3 bounded
query engine.
"""
from __future__ import annotations

import re

from contracts.world_understanding.query import WorldQuery
from contracts.world_understanding.repository_query import RepositoryGraphQuery
from world_understanding.software_world.graph import SparseWorldGraph
from world_understanding.software_world.query import execute_repository_graph_query

from .enrichment import ContextProjectionCandidate

_TOKEN_RE = re.compile(r"[\w./:@-]+", re.UNICODE)
_GENERIC_TOKENS = frozenset({
    "the", "this", "that", "with", "from", "into", "about", "code", "repo",
    "repository", "file", "function", "class", "module", "test", "tests",
    "代码", "仓库", "文件", "函数", "模块", "测试", "看看", "分析", "修改", "影响",
})


def _focus_tokens(focus: str) -> tuple[str, ...]:
    tokens = {
        token.casefold()
        for token in _TOKEN_RE.findall(str(focus or ""))
        if len(token.strip()) >= 3 and token.casefold() not in _GENERIC_TOKENS
    }
    return tuple(sorted(tokens))


def _entity_strings(entity) -> tuple[str, ...]:
    values = {entity.entity_id.casefold(), entity.canonical_name.casefold()}
    values.update(str(alias).casefold() for alias in entity.aliases)
    return tuple(sorted(value for value in values if value))


def _entity_focus_score(entity, focus: str, tokens: tuple[str, ...]) -> int:
    focus_folded = str(focus or "").strip().casefold()
    values = _entity_strings(entity)
    if focus_folded and focus_folded in values:
        return 1000
    score = 0
    for token in tokens:
        for value in values:
            if token == value:
                score = max(score, 980)
            elif token in value:
                score = max(score, 820 if len(token) >= 5 else 720)
            elif value in token and len(value) >= 5:
                score = max(score, 760)
    return score


def _entity_summary(entity, *, seed: bool) -> str:
    role = "focus" if seed else "neighbor"
    return (
        f"Repository {role} {entity.entity_type} '{entity.canonical_name}' "
        f"is present in the committed software-world frame at revision {entity.revision}."
    )


def _relation_summary(graph: SparseWorldGraph, relation) -> str:
    subject = graph.entity(relation.subject_ref.record_id)
    target_id = relation.value.entity_ref if relation.value.kind == "entity_ref" else None
    target = None if target_id is None else graph.entity(target_id)
    subject_name = relation.subject_ref.record_id if subject is None else subject.canonical_name
    target_name = str(target_id or relation.value.kind) if target is None else target.canonical_name
    return f"Repository relation '{subject_name}' --{relation.predicate}--> '{target_name}'."


def build_repository_context_candidates(
    graph: SparseWorldGraph,
    query: WorldQuery,
    *,
    max_seeds: int = 4,
    max_entities: int = 24,
    max_relations: int = 32,
    max_operations: int = 256,
) -> tuple[ContextProjectionCandidate, ...]:
    """Return bounded deterministic summary overrides for existing graph records."""
    if not query.has_valid_hash():
        raise ValueError("WORLD_QUERY_HASH_INVALID")
    if query.scope != graph.scope:
        raise ValueError("REPOSITORY_CONTEXT_SCOPE_MISMATCH")
    if not 1 <= max_seeds <= 16:
        raise ValueError("REPOSITORY_CONTEXT_SEED_LIMIT_INVALID")

    tokens = _focus_tokens(query.focus)
    if not tokens:
        return ()

    scored = []
    for entity in graph.entities():
        if entity.lifecycle != "ACTIVE":
            continue
        score = _entity_focus_score(entity, query.focus, tokens)
        if score > 0:
            scored.append((score, entity.entity_id, entity))
    scored.sort(key=lambda row: (-row[0], row[1]))
    seeds = tuple(row[2] for row in scored[:max_seeds])
    if not seeds:
        return ()

    repository_query = RepositoryGraphQuery.build(
        scope=graph.scope,
        frame_id=graph.frame_id,
        frame_revision_hash=graph.frame_revision_hash,
        seed_tokens=tuple(sorted(entity.entity_id for entity in seeds)),
        mode="NEIGHBORHOOD",
        direction="BOTH",
        max_depth=1,
        max_entities=max_entities,
        max_relations=max_relations,
        max_operations=max_operations,
        include_retired=False,
    )
    result = execute_repository_graph_query(graph, repository_query)
    seed_keys = {ref.sort_key() for ref in result.matched_seed_refs}

    candidates: list[ContextProjectionCandidate] = []
    for ref in result.entity_refs:
        entity = graph.entity(ref.record_id)
        if entity is None:
            continue
        seed = ref.sort_key() in seed_keys
        candidates.append(ContextProjectionCandidate(
            ref=ref,
            item_kind="repository_focus" if seed else "repository_neighbor",
            summary=_entity_summary(entity, seed=seed),
            task_relevance_milli=1000 if seed else 880,
            impact_milli=820 if seed else 700,
            freshness_need_milli=900,
        ))

    for ref in result.relation_refs:
        relation = graph.relation(ref.record_id)
        if relation is None:
            continue
        candidates.append(ContextProjectionCandidate(
            ref=ref,
            item_kind="repository_relation",
            summary=_relation_summary(graph, relation),
            task_relevance_milli=840,
            impact_milli=780,
            freshness_need_milli=900,
        ))

    by_ref = {}
    for candidate in sorted(candidates, key=lambda item: item.priority_key()):
        by_ref.setdefault(candidate.ref.sort_key(), candidate)
    return tuple(sorted(by_ref.values(), key=lambda item: item.priority_key()))


__all__ = ["build_repository_context_candidates"]
