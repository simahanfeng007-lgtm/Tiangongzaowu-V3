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
from world_understanding.software_world.entity import entity_ref
from world_understanding.software_world.query import (
    execute_repository_graph_query,
    relation_ref,
)

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


def _entity_summary(entity, *, seed: bool, score_milli: int | None = None) -> str:
    role = "focus" if seed else "neighbor"
    canonical_name = _untrusted_repository_text(entity.canonical_name)
    ranking = "" if score_milli is None else f" Weighted relevance={score_milli}/1000."
    attributes = {
        item.key: item.value.string_value
        for item in getattr(entity, "attributes", ())
        if item.value.kind == "string" and item.value.string_value is not None
    }
    coverage = attributes.get("coverage_state")
    coverage_text = "" if coverage is None else f" Tree coverage={coverage}."
    return (
        f"[UNTRUSTED_REPOSITORY_DATA] Repository {role} {entity.entity_type} "
        f"'{canonical_name}' "
        f"is present in the committed software-world frame at revision {entity.revision}."
        + coverage_text
        + ranking
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


def _repository_tree_ancestors(
    graph: SparseWorldGraph,
    seeds: tuple[object, ...],
    *,
    max_entities: int,
    max_relations: int,
) -> tuple[tuple[object, ...], tuple[object, ...]]:
    """Walk the unique inbound total-part chain before horizontal expansion."""
    entities: dict[str, object] = {}
    relations: dict[str, object] = {}
    for seed in seeds:
        current = seed
        visited = {seed.entity_id}
        while len(entities) < max_entities and len(relations) < max_relations:
            parents = []
            for relation in graph.relations_touching(current.entity_id):
                if (
                    relation.predicate != "CONTAINS"
                    or relation.value.kind != "entity_ref"
                    or relation.value.entity_ref != current.entity_id
                ):
                    continue
                parent = graph.entity(relation.subject_ref.record_id)
                if (
                    parent is not None
                    and parent.lifecycle == "ACTIVE"
                    and parent.entity_type in {"Repository", "RepositoryBranch"}
                ):
                    parents.append((relation, parent))
            if len(parents) != 1:
                break
            relation, parent = parents[0]
            if parent.entity_id in visited:
                break
            visited.add(parent.entity_id)
            entities[parent.entity_id] = parent
            relations[relation.relation_id] = relation
            current = parent
    return (
        tuple(entities[key] for key in sorted(entities)),
        tuple(relations[key] for key in sorted(relations)),
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

    tree_entities, tree_relations = _repository_tree_ancestors(
        graph,
        seeds,
        max_entities=max_entities,
        max_relations=max_relations,
    )
    remaining_entities = max(1, max_entities - len(tree_entities))
    remaining_relations = max(1, max_relations - len(tree_relations))
    repository_query = RepositoryGraphQuery.build(
        scope=graph.scope,
        frame_id=graph.frame_id,
        frame_revision_hash=graph.frame_revision_hash,
        seed_tokens=tuple(sorted(entity.entity_id for entity in seeds)),
        mode="ASSOCIATIVE",
        direction="BOTH",
        max_depth=1,
        max_entities=remaining_entities,
        max_relations=remaining_relations,
        max_operations=max_operations,
        include_retired=False,
    )
    result = execute_repository_graph_query(graph, repository_query)
    seed_keys = {ref.sort_key() for ref in result.matched_seed_refs}
    rank_by_entity = {
        item.entity_ref.record_id: item for item in result.ranked_evidence
    }

    candidates: list[ContextProjectionCandidate] = []
    for entity in tree_entities:
        candidates.append(ContextProjectionCandidate(
            ref=entity_ref(entity),
            item_kind="repository_tree_ancestor",
            summary=_entity_summary(entity, seed=False, score_milli=980),
            task_relevance_milli=980,
            impact_milli=900,
            freshness_need_milli=900,
        ))
    for relation in tree_relations:
        candidates.append(ContextProjectionCandidate(
            ref=relation_ref(relation),
            item_kind="repository_tree_relation",
            summary=_relation_summary(graph, relation),
            task_relevance_milli=960,
            impact_milli=880,
            freshness_need_milli=900,
        ))
    for ref in result.entity_refs:
        entity = graph.entity(ref.record_id)
        if entity is None:
            continue
        seed = ref.sort_key() in seed_keys
        ranking = rank_by_entity.get(ref.record_id)
        score = 1000 if ranking is None and seed else (
            0 if ranking is None else ranking.score_milli
        )
        candidates.append(ContextProjectionCandidate(
            ref=ref,
            item_kind="repository_focus" if seed else "repository_neighbor",
            summary=_entity_summary(entity, seed=seed, score_milli=score),
            task_relevance_milli=(
                1000 if seed else min(980, 650 + score * 330 // 1000)
            ),
            impact_milli=820 if seed else min(900, 600 + score * 300 // 1000),
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
