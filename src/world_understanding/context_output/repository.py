"""Bounded repository-context enrichment from the committed Software World graph.

No filesystem, Git, network, model, Gateway, scheduler, mutable repository
index, or whole-graph seed scan is permitted here. Seed discovery uses the
Software World token index with strict fanout limits, then delegates traversal
to the M3 bounded query engine.
"""
from __future__ import annotations

import json
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
    "inspect", "impact", "analyze", "change", "changed",
    "代码", "仓库", "文件", "函数", "模块", "测试", "看看", "分析", "修改", "影响",
})
_MAX_FOCUS_TOKENS = 16
_MAX_TOKEN_MATCHES = 16


def _untrusted_repository_text(value: object, *, limit: int = 512) -> str:
    text = str(value or "")[:limit]
    return json.dumps(text, ensure_ascii=True)[1:-1]


def _focus_tokens(focus: str) -> tuple[str, ...]:
    values: set[str] = set()
    for raw in _TOKEN_RE.findall(str(focus or "")):
        token = raw.strip()
        if len(token) < 3 or token.casefold() in _GENERIC_TOKENS:
            continue
        values.add(token)
    # Prefer code-shaped and more specific tokens. Query.focus itself is already
    # contract-bounded; this additionally caps graph-index probes.
    ordered = sorted(
        values,
        key=lambda token: (
            0 if any(mark in token for mark in (".", "/", "\\", ":", "@")) else 1,
            -len(token),
            token.casefold(),
            token,
        ),
    )
    return tuple(ordered[:_MAX_FOCUS_TOKENS])


def _token_variants(token: str) -> tuple[str, ...]:
    folded = token.casefold()
    return (token,) if folded == token else (token, folded)


def _resolve_seed_entities(
    graph: SparseWorldGraph,
    tokens: tuple[str, ...],
    *,
    max_seeds: int,
) -> tuple[object, ...]:
    """Resolve only unambiguous exact tokens; never guess or scan all entities."""
    by_id: dict[str, object] = {}
    for token in tokens:
        resolved_for_token: dict[str, object] = {}
        overflow = False
        for variant in _token_variants(token):
            try:
                matches = graph.resolve_token_bounded(
                    variant,
                    max_matches=_MAX_TOKEN_MATCHES,
                )
            except ValueError as exc:
                if str(exc) != "SOFTWARE_WORLD_TOKEN_MATCH_LIMIT_EXCEEDED":
                    raise
                overflow = True
                break
            for entity in matches:
                resolved_for_token[entity.entity_id] = entity
        if overflow or len(resolved_for_token) != 1:
            # Ambiguous or excessive fanout requires a more exact query.  Context
            # enrichment is optional, so it never guesses among identities.
            continue
        entity = next(iter(resolved_for_token.values()))
        by_id.setdefault(entity.entity_id, entity)
        if len(by_id) >= max_seeds:
            break
    return tuple(by_id[key] for key in sorted(by_id))


def _entity_summary(entity, *, seed: bool) -> str:
    role = "focus" if seed else "neighbor"
    canonical_name = _untrusted_repository_text(entity.canonical_name)
    return (
        f"[UNTRUSTED_REPOSITORY_DATA] Repository {role} {entity.entity_type} "
        f"'{canonical_name}' "
        f"is present in the committed software-world frame at revision {entity.revision}."
    )


def _relation_summary(graph: SparseWorldGraph, relation) -> str:
    subject = graph.entity(relation.subject_ref.record_id)
    target_id = relation.value.entity_ref if relation.value.kind == "entity_ref" else None
    target = None if target_id is None else graph.entity(target_id)
    subject_name = relation.subject_ref.record_id if subject is None else subject.canonical_name
    target_name = str(target_id or relation.value.kind) if target is None else target.canonical_name
    return (
        "[UNTRUSTED_REPOSITORY_DATA] Repository relation "
        f"'{_untrusted_repository_text(subject_name)}' "
        f"--{_untrusted_repository_text(relation.predicate)}--> "
        f"'{_untrusted_repository_text(target_name)}'."
    )


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
    seeds = _resolve_seed_entities(graph, tokens, max_seeds=max_seeds)
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
