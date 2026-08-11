"""Read-only bounded repository graph query contracts for P14 M3.

These contracts describe deterministic queries over an already-materialized
Software World Graph. They carry no execution authority and never authorize,
mutate, scan, fetch, schedule, or learn.
"""
from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from ..canonical import canonical_sha256
from ..models import OpaqueId, Sha256
from ._base import (
    HashedWorldContract,
    MAX_SAFE_INTEGER,
    WorldRecordRef,
    normalized_text,
    sorted_unique_refs,
    sorted_unique_strings,
)
from .scope import WorldScope

RepositoryQueryMode = Literal["NEIGHBORHOOD", "IMPACT", "ASSOCIATIVE"]
RepositoryQueryDirection = Literal["OUTBOUND", "INBOUND", "BOTH"]
RepositoryTraversalDirection = Literal["OUTBOUND", "INBOUND"]
RepositoryQueryTruncationReason = Literal[
    "ENTITY_BUDGET",
    "RELATION_BUDGET",
    "OPERATION_BUDGET",
]


def derive_repository_graph_query_id(
    *,
    world_scope_hash: str,
    frame_id: str,
    frame_revision_hash: str,
    seed_tokens: tuple[str, ...],
    mode: str,
    direction: str,
    relation_predicates: tuple[str, ...],
    max_depth: int,
    max_entities: int,
    max_relations: int,
    max_operations: int,
    include_retired: bool,
) -> str:
    return "rqry_" + canonical_sha256({
        "domain": "tiangong.repository-graph-query-id.v1",
        "world_scope_hash": world_scope_hash,
        "frame_id": frame_id,
        "frame_revision_hash": frame_revision_hash,
        "seed_tokens": seed_tokens,
        "mode": mode,
        "direction": direction,
        "relation_predicates": relation_predicates,
        "max_depth": max_depth,
        "max_entities": max_entities,
        "max_relations": max_relations,
        "max_operations": max_operations,
        "include_retired": include_retired,
    })


def derive_repository_graph_result_id(*, query_id: str, query_sha256: str) -> str:
    return "rgrs_" + canonical_sha256({
        "domain": "tiangong.repository-graph-result-id.v1",
        "query_id": query_id,
        "query_sha256": query_sha256,
    })


class RepositoryGraphQuery(HashedWorldContract):
    _hash_field = "query_sha256"

    query_id: OpaqueId
    scope: WorldScope
    frame_id: OpaqueId
    frame_revision_hash: Sha256
    seed_tokens: tuple[str, ...] = Field(min_length=1, max_length=64)
    mode: RepositoryQueryMode = "NEIGHBORHOOD"
    direction: RepositoryQueryDirection = "BOTH"
    relation_predicates: tuple[OpaqueId, ...] = Field(default=(), max_length=64)
    max_depth: int = Field(default=1, ge=0, le=4, strict=True)
    max_entities: int = Field(default=64, ge=1, le=512, strict=True)
    max_relations: int = Field(default=128, ge=1, le=1024, strict=True)
    max_operations: int = Field(default=2048, ge=1, le=100_000, strict=True)
    include_retired: bool = False
    context_only: Literal[True] = True
    may_authorize: Literal[False] = False
    may_execute: Literal[False] = False
    empirical_evidence_weight_milli: Literal[0] = 0
    query_sha256: Sha256

    @field_validator("seed_tokens")
    @classmethod
    def validate_seed_tokens(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(normalized_text(item) for item in value)
        return sorted_unique_strings(normalized)

    @field_validator("relation_predicates")
    @classmethod
    def validate_predicates(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return sorted_unique_strings(tuple(normalized_text(item) for item in value))

    @model_validator(mode="after")
    def validate_identity_and_mode(self) -> Self:
        expected = derive_repository_graph_query_id(
            world_scope_hash=self.scope.world_scope_hash,
            frame_id=self.frame_id,
            frame_revision_hash=self.frame_revision_hash,
            seed_tokens=self.seed_tokens,
            mode=self.mode,
            direction=self.direction,
            relation_predicates=self.relation_predicates,
            max_depth=self.max_depth,
            max_entities=self.max_entities,
            max_relations=self.max_relations,
            max_operations=self.max_operations,
            include_retired=self.include_retired,
        )
        if self.query_id != expected:
            raise ValueError("repository graph query id mismatch")
        if self.mode == "IMPACT" and self.direction != "INBOUND":
            raise ValueError("impact query must use inbound traversal")
        return self

    @classmethod
    def build(
        cls,
        *,
        scope: WorldScope,
        frame_id: str,
        frame_revision_hash: str,
        seed_tokens: tuple[str, ...],
        mode: RepositoryQueryMode = "NEIGHBORHOOD",
        direction: RepositoryQueryDirection = "BOTH",
        relation_predicates: tuple[str, ...] = (),
        max_depth: int = 1,
        max_entities: int = 64,
        max_relations: int = 128,
        max_operations: int = 2048,
        include_retired: bool = False,
    ) -> "RepositoryGraphQuery":
        seed_tokens = tuple(sorted(set(normalized_text(item) for item in seed_tokens)))
        relation_predicates = tuple(
            sorted(set(normalized_text(item) for item in relation_predicates))
        )
        query_id = derive_repository_graph_query_id(
            world_scope_hash=scope.world_scope_hash,
            frame_id=frame_id,
            frame_revision_hash=frame_revision_hash,
            seed_tokens=seed_tokens,
            mode=mode,
            direction=direction,
            relation_predicates=relation_predicates,
            max_depth=max_depth,
            max_entities=max_entities,
            max_relations=max_relations,
            max_operations=max_operations,
            include_retired=include_retired,
        )
        return cls(
            query_id=query_id,
            scope=scope,
            frame_id=frame_id,
            frame_revision_hash=frame_revision_hash,
            seed_tokens=seed_tokens,
            mode=mode,
            direction=direction,
            relation_predicates=relation_predicates,
            max_depth=max_depth,
            max_entities=max_entities,
            max_relations=max_relations,
            max_operations=max_operations,
            include_retired=include_retired,
            query_sha256="0" * 64,
        ).with_computed_hash()


class RepositoryTraversalStep(HashedWorldContract):
    _hash_field = "step_sha256"

    depth: int = Field(ge=1, le=4, strict=True)
    direction: RepositoryTraversalDirection
    from_entity_ref: WorldRecordRef
    relation_ref: WorldRecordRef
    to_entity_ref: WorldRecordRef
    step_sha256: Sha256

    def sort_key(self) -> tuple:
        return (
            self.depth,
            self.from_entity_ref.sort_key(),
            self.relation_ref.sort_key(),
            self.to_entity_ref.sort_key(),
            self.direction,
        )


class RepositoryRankedEvidence(HashedWorldContract):
    """Deterministic explanation for one weighted associative result."""

    _hash_field = "evidence_sha256"

    entity_ref: WorldRecordRef
    score_milli: int = Field(ge=0, le=1000, strict=True)
    seed_distance: int = Field(ge=0, le=4, strict=True)
    matched_seed_count: int = Field(ge=1, le=64, strict=True)
    strongest_predicate: str | None = Field(default=None, max_length=160)
    path_relation_refs: tuple[WorldRecordRef, ...] = Field(default=(), max_length=4)
    evidence_sha256: Sha256

    @field_validator("strongest_predicate")
    @classmethod
    def validate_predicate(cls, value: str | None) -> str | None:
        return None if value is None else normalized_text(value)

    @field_validator("path_relation_refs")
    @classmethod
    def validate_path(
        cls, value: tuple[WorldRecordRef, ...]
    ) -> tuple[WorldRecordRef, ...]:
        if len({item.sort_key() for item in value}) != len(value):
            raise ValueError("ranked evidence path may not repeat relations")
        return value

    def sort_key(self) -> tuple:
        return (-self.score_milli, self.seed_distance, self.entity_ref.sort_key())


class RepositoryGraphQueryResult(HashedWorldContract):
    _hash_field = "result_sha256"

    result_id: OpaqueId
    query_id: OpaqueId
    query_sha256: Sha256
    scope: WorldScope
    frame_id: OpaqueId
    frame_revision_hash: Sha256
    matched_seed_refs: tuple[WorldRecordRef, ...] = Field(default=(), max_length=64)
    ambiguous_seed_tokens: tuple[str, ...] = Field(default=(), max_length=64)
    unresolved_seed_tokens: tuple[str, ...] = Field(default=(), max_length=64)
    entity_refs: tuple[WorldRecordRef, ...] = Field(default=(), max_length=512)
    relation_refs: tuple[WorldRecordRef, ...] = Field(default=(), max_length=1024)
    traversal_steps: tuple[RepositoryTraversalStep, ...] = Field(default=(), max_length=1024)
    ranked_evidence: tuple[RepositoryRankedEvidence, ...] = Field(default=(), max_length=512)
    max_depth_reached: int = Field(ge=0, le=4, strict=True)
    operation_count: int = Field(ge=0, le=MAX_SAFE_INTEGER, strict=True)
    truncated: bool
    truncation_reasons: tuple[RepositoryQueryTruncationReason, ...] = ()
    context_only: Literal[True] = True
    may_authorize: Literal[False] = False
    may_execute: Literal[False] = False
    empirical_evidence_weight_milli: Literal[0] = 0
    result_sha256: Sha256

    _validate_matched = field_validator("matched_seed_refs")(sorted_unique_refs)
    _validate_entities = field_validator("entity_refs")(sorted_unique_refs)
    _validate_relations = field_validator("relation_refs")(sorted_unique_refs)

    @field_validator("ambiguous_seed_tokens", "unresolved_seed_tokens")
    @classmethod
    def validate_seed_status_tokens(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return sorted_unique_strings(tuple(normalized_text(item) for item in value))

    @field_validator("truncation_reasons")
    @classmethod
    def validate_truncation_reasons(
        cls, value: tuple[RepositoryQueryTruncationReason, ...]
    ) -> tuple[RepositoryQueryTruncationReason, ...]:
        if value != tuple(sorted(set(value))):
            raise ValueError("truncation reasons must be sorted and unique")
        return value

    @field_validator("traversal_steps")
    @classmethod
    def validate_steps(
        cls, value: tuple[RepositoryTraversalStep, ...]
    ) -> tuple[RepositoryTraversalStep, ...]:
        keys = tuple(item.sort_key() for item in value)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("traversal steps must be sorted and unique")
        return value

    @field_validator("ranked_evidence")
    @classmethod
    def validate_ranked_evidence(
        cls, value: tuple[RepositoryRankedEvidence, ...]
    ) -> tuple[RepositoryRankedEvidence, ...]:
        keys = tuple(item.sort_key() for item in value)
        if keys != tuple(sorted(keys)):
            raise ValueError("ranked evidence must be in deterministic score order")
        if len({item.entity_ref.sort_key() for item in value}) != len(value):
            raise ValueError("ranked evidence entities must be unique")
        return value

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        if self.result_id != derive_repository_graph_result_id(
            query_id=self.query_id, query_sha256=self.query_sha256
        ):
            raise ValueError("repository graph result id mismatch")
        if self.truncated != bool(self.truncation_reasons):
            raise ValueError("repository graph truncation state mismatch")
        entity_keys = {ref.sort_key() for ref in self.entity_refs}
        if any(ref.sort_key() not in entity_keys for ref in self.matched_seed_refs):
            raise ValueError("matched seed ref missing from entity result set")
        if any(
            item.entity_ref.sort_key() not in entity_keys
            for item in self.ranked_evidence
        ):
            raise ValueError("ranked evidence ref missing from entity result set")
        return self


__all__ = [
    "RepositoryGraphQuery",
    "RepositoryGraphQueryResult",
    "RepositoryRankedEvidence",
    "RepositoryQueryDirection",
    "RepositoryQueryMode",
    "RepositoryQueryTruncationReason",
    "RepositoryTraversalDirection",
    "RepositoryTraversalStep",
    "derive_repository_graph_query_id",
    "derive_repository_graph_result_id",
]
