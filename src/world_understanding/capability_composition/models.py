"""Immutable candidate and compile-context records for P4.

These records are non-authorizing projections used by the proposal parser,
composition compiler, and conservative validator. They do not route, execute,
grant, ticket, persist, or mutate WorldState.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import re
from typing import Any, Iterable

from contracts import ActionRegistrySnapshot, canonical_sha256
from contracts.capability_composition import (
    SkillSourcePrimitiveV1,
    SourceRevisionRefV1,
    ToolSourcePrimitiveV1,
)
from world_understanding.skill_method_world import SkillMethodWorldSnapshotV1
from world_understanding.tool_capability_world import ToolCapabilityWorldSnapshotV1


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,159}$")
_REQUEST_ID = re.compile(r"^req_[0-9a-f]{64}$")
_RUN_ID = re.compile(r"^run_[0-9a-f]{64}$")
_METHOD_CANDIDATE_ID = re.compile(r"^M[0-9]{2}$")
_ACTION_CANDIDATE_ID = re.compile(r"^A[0-9]{2}$")

MAX_METHOD_CANDIDATES = 15
MAX_ACTION_CANDIDATES = 30


class CapabilityCompositionError(ValueError):
    """Base error for deterministic P4 composition processing."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(code if not detail else f"{code}: {detail}")


def _require_sha256(value: str, field: str) -> None:
    if _SHA256.fullmatch(value) is None:
        raise CapabilityCompositionError("composition.hash.invalid", field)


def _require_opaque(value: str, field: str) -> None:
    if _OPAQUE_ID.fullmatch(value) is None:
        raise CapabilityCompositionError("composition.identity.invalid", field)


def _primitive_payload(value: SkillSourcePrimitiveV1 | ToolSourcePrimitiveV1) -> dict[str, Any]:
    return value.model_dump(mode="json")


@dataclass(frozen=True, slots=True)
class MethodCandidateBindingV1:
    candidate_id: str
    primitive: SkillSourcePrimitiveV1
    binding_sha256: str
    may_authorize: bool = False
    may_execute: bool = False

    def __post_init__(self) -> None:
        if _METHOD_CANDIDATE_ID.fullmatch(self.candidate_id) is None:
            raise CapabilityCompositionError(
                "candidate.method_id.invalid", self.candidate_id
            )
        if self.may_authorize or self.may_execute:
            raise CapabilityCompositionError("candidate.method.authority_forbidden")
        if (
            self.primitive.source_ref.source_kind != "SKILL_METHOD"
            or self.primitive.source_ref.semantic_id != self.primitive.method_id
            or self.primitive.source_ref.version != self.primitive.version
            or self.primitive.source_ref.manifest_sha256 is not None
            or self.primitive.source_sha256 != self.primitive.source_ref.source_sha256
            or self.primitive.descriptor_sha256
            != self.primitive.source_ref.descriptor_sha256
        ):
            raise CapabilityCompositionError(
                "candidate.method_source.invalid", self.primitive.method_id
            )
        _require_sha256(self.binding_sha256, "method candidate binding")

    def payload(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "primitive": _primitive_payload(self.primitive),
            "may_authorize": self.may_authorize,
            "may_execute": self.may_execute,
        }

    def computed_sha256(self) -> str:
        return canonical_sha256(self.payload())

    def has_valid_sha256(self) -> bool:
        return self.binding_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> "MethodCandidateBindingV1":
        return replace(self, binding_sha256=self.computed_sha256())


@dataclass(frozen=True, slots=True)
class ActionCandidateBindingV1:
    candidate_id: str
    primitive: ToolSourcePrimitiveV1
    source_revision: SourceRevisionRefV1
    binding_sha256: str
    may_authorize: bool = False
    may_execute: bool = False

    def __post_init__(self) -> None:
        if _ACTION_CANDIDATE_ID.fullmatch(self.candidate_id) is None:
            raise CapabilityCompositionError(
                "candidate.action_id.invalid", self.candidate_id
            )
        if self.may_authorize or self.may_execute:
            raise CapabilityCompositionError("candidate.action.authority_forbidden")
        if (
            self.source_revision.source_kind != "TOOL_ACTION"
            or self.source_revision.semantic_id != self.primitive.action_id
            or self.source_revision.version != self.primitive.action_version
            or self.source_revision.manifest_sha256
            != self.primitive.action_manifest_sha256
            or self.source_revision.descriptor_sha256
            != self.primitive.descriptor_sha256
        ):
            raise CapabilityCompositionError(
                "candidate.action_source.invalid", self.primitive.action_id
            )
        _require_sha256(self.binding_sha256, "action candidate binding")

    def payload(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "primitive": _primitive_payload(self.primitive),
            "source_revision": self.source_revision.model_dump(mode="json"),
            "may_authorize": self.may_authorize,
            "may_execute": self.may_execute,
        }

    def computed_sha256(self) -> str:
        return canonical_sha256(self.payload())

    def has_valid_sha256(self) -> bool:
        return self.binding_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> "ActionCandidateBindingV1":
        return replace(self, binding_sha256=self.computed_sha256())


@dataclass(frozen=True, slots=True)
class CompositionCandidateSnapshotV1:
    schema: str
    tool_world_sha256: str
    method_world_sha256: str
    method_candidates: tuple[MethodCandidateBindingV1, ...]
    action_candidates: tuple[ActionCandidateBindingV1, ...]
    candidate_snapshot_sha256: str
    may_authorize: bool = False
    may_execute: bool = False

    def __post_init__(self) -> None:
        if self.schema != "tiangong.composition-candidates.v1":
            raise CapabilityCompositionError("candidate.snapshot.schema.invalid")
        if self.may_authorize or self.may_execute:
            raise CapabilityCompositionError("candidate.snapshot.authority_forbidden")
        _require_sha256(self.tool_world_sha256, "tool world")
        _require_sha256(self.method_world_sha256, "method world")
        _require_sha256(self.candidate_snapshot_sha256, "candidate snapshot")

        method_ids = tuple(item.candidate_id for item in self.method_candidates)
        action_ids = tuple(item.candidate_id for item in self.action_candidates)
        if (
            method_ids != tuple(sorted(set(method_ids)))
            or len(method_ids) > MAX_METHOD_CANDIDATES
        ):
            raise CapabilityCompositionError("candidate.method_set.invalid")
        if (
            not action_ids
            or action_ids != tuple(sorted(set(action_ids)))
            or len(action_ids) > MAX_ACTION_CANDIDATES
        ):
            raise CapabilityCompositionError("candidate.action_set.invalid")
        if any(not item.has_valid_sha256() for item in self.method_candidates):
            raise CapabilityCompositionError("candidate.method_binding.hash_invalid")
        if any(not item.has_valid_sha256() for item in self.action_candidates):
            raise CapabilityCompositionError("candidate.action_binding.hash_invalid")
        method_semantic_ids = tuple(
            item.primitive.method_id for item in self.method_candidates
        )
        action_semantic_ids = tuple(
            item.primitive.action_id for item in self.action_candidates
        )
        if len(method_semantic_ids) != len(set(method_semantic_ids)):
            raise CapabilityCompositionError("candidate.method_semantic.duplicate")
        if len(action_semantic_ids) != len(set(action_semantic_ids)):
            raise CapabilityCompositionError("candidate.action_semantic.duplicate")

    def payload(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "tool_world_sha256": self.tool_world_sha256,
            "method_world_sha256": self.method_world_sha256,
            "may_authorize": self.may_authorize,
            "may_execute": self.may_execute,
            "method_candidates": [
                {**item.payload(), "binding_sha256": item.binding_sha256}
                for item in self.method_candidates
            ],
            "action_candidates": [
                {**item.payload(), "binding_sha256": item.binding_sha256}
                for item in self.action_candidates
            ],
        }

    def computed_sha256(self) -> str:
        return canonical_sha256(self.payload())

    def has_valid_sha256(self) -> bool:
        return self.candidate_snapshot_sha256 == self.computed_sha256()

    def method_by_candidate(self) -> dict[str, MethodCandidateBindingV1]:
        return {item.candidate_id: item for item in self.method_candidates}

    def action_by_candidate(self) -> dict[str, ActionCandidateBindingV1]:
        return {item.candidate_id: item for item in self.action_candidates}


@dataclass(frozen=True, slots=True)
class CompositionCompileContextV1:
    schema: str
    request_id: str
    run_id: str
    generation: int
    principal_scope_hash: str
    world_state_ref: str
    world_state_sha256: str
    goal_ref: str
    goal_fingerprint: str
    environment_class: str
    context_fingerprint_sha256: str
    capability_manifest_sha256: str
    created_at_ms: int
    context_sha256: str

    def __post_init__(self) -> None:
        if self.schema != "tiangong.composition-compile-context.v1":
            raise CapabilityCompositionError("compile_context.schema.invalid")
        if _REQUEST_ID.fullmatch(self.request_id) is None:
            raise CapabilityCompositionError("compile_context.request_id.invalid")
        if _RUN_ID.fullmatch(self.run_id) is None:
            raise CapabilityCompositionError("compile_context.run_id.invalid")
        if self.generation < 0 or self.created_at_ms < 0:
            raise CapabilityCompositionError("compile_context.counter.invalid")
        _require_opaque(self.world_state_ref, "world_state_ref")
        _require_opaque(self.goal_ref, "goal_ref")
        _require_opaque(self.environment_class, "environment_class")
        for field, value in (
            ("principal_scope_hash", self.principal_scope_hash),
            ("world_state_sha256", self.world_state_sha256),
            ("goal_fingerprint", self.goal_fingerprint),
            ("context_fingerprint_sha256", self.context_fingerprint_sha256),
            ("capability_manifest_sha256", self.capability_manifest_sha256),
            ("context_sha256", self.context_sha256),
        ):
            _require_sha256(value, field)

    def payload(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "request_id": self.request_id,
            "run_id": self.run_id,
            "generation": self.generation,
            "principal_scope_hash": self.principal_scope_hash,
            "world_state_ref": self.world_state_ref,
            "world_state_sha256": self.world_state_sha256,
            "goal_ref": self.goal_ref,
            "goal_fingerprint": self.goal_fingerprint,
            "environment_class": self.environment_class,
            "context_fingerprint_sha256": self.context_fingerprint_sha256,
            "capability_manifest_sha256": self.capability_manifest_sha256,
            "created_at_ms": self.created_at_ms,
        }

    def computed_sha256(self) -> str:
        return canonical_sha256(self.payload())

    def has_valid_sha256(self) -> bool:
        return self.context_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> "CompositionCompileContextV1":
        return replace(self, context_sha256=self.computed_sha256())


def derive_action_source_revision(
    primitive: ToolSourcePrimitiveV1,
) -> SourceRevisionRefV1:
    """Derive a SourceRevisionRef from the already-validated P2 primitive."""

    files = tuple(sorted({item.path for item in primitive.implementation_refs}))
    if not files:
        raise CapabilityCompositionError(
            "candidate.action_source.empty", primitive.action_id
        )
    implementation_hashes = tuple(sorted(set(primitive.implementation_hashes)))
    if not implementation_hashes:
        raise CapabilityCompositionError(
            "candidate.action_source.hash_empty", primitive.action_id
        )
    if len(implementation_hashes) == 1:
        source_sha256 = implementation_hashes[0]
    else:
        source_sha256 = canonical_sha256(
            {
                "domain": "tiangong.tool-source-revision-derived.v1",
                "implementation_hashes": list(implementation_hashes),
                "implementation_refs": [
                    item.model_dump(mode="json")
                    for item in sorted(
                        primitive.implementation_refs,
                        key=lambda item: (
                            item.path,
                            item.start_line or 0,
                            item.end_line or 0,
                        ),
                    )
                ],
            }
        )
    return SourceRevisionRefV1(
        source_kind="TOOL_ACTION",
        semantic_id=primitive.action_id,
        version=primitive.action_version,
        source_files=files,
        source_spans=tuple(
            sorted(
                primitive.implementation_refs,
                key=lambda item: (
                    item.path,
                    item.start_line or 0,
                    item.end_line or 0,
                ),
            )
        ),
        source_sha256=source_sha256,
        descriptor_sha256=primitive.descriptor_sha256,
        manifest_sha256=primitive.action_manifest_sha256,
    )


def _normalize_selection(
    values: Iterable[str] | None,
    *,
    available: set[str],
    limit: int,
    kind: str,
) -> tuple[str, ...]:
    if values is None:
        selected = tuple(sorted(available))
    else:
        raw = tuple(values)
        if len(raw) != len(set(raw)):
            raise CapabilityCompositionError(f"candidate.{kind}.selection_duplicate")
        selected = tuple(sorted(raw))
    if len(selected) > limit:
        raise CapabilityCompositionError(f"candidate.{kind}.budget_exceeded")
    missing = tuple(value for value in selected if value not in available)
    if missing:
        raise CapabilityCompositionError(
            f"candidate.{kind}.selection_missing", ",".join(missing)
        )
    return selected


def build_candidate_snapshot(
    tool_world: ToolCapabilityWorldSnapshotV1,
    method_world: SkillMethodWorldSnapshotV1,
    *,
    method_ids: Iterable[str] | None = None,
    action_ids: Iterable[str] | None = None,
) -> CompositionCandidateSnapshotV1:
    """Bind a bounded candidate set from P2/P3 read-only world projections."""

    if not tool_world.has_valid_sha256():
        raise CapabilityCompositionError("candidate.tool_world.hash_invalid")
    if not method_world.has_valid_sha256():
        raise CapabilityCompositionError("candidate.method_world.hash_invalid")
    if tool_world.may_authorize or tool_world.may_execute:
        raise CapabilityCompositionError("candidate.tool_world.authority_forbidden")
    if method_world.may_authorize or method_world.may_execute:
        raise CapabilityCompositionError("candidate.method_world.authority_forbidden")

    methods_by_id = {item.method_id: item for item in method_world.primitives}
    actions_by_id = {item.action_id: item for item in tool_world.primitives}
    selected_methods = _normalize_selection(
        method_ids,
        available=set(methods_by_id),
        limit=MAX_METHOD_CANDIDATES,
        kind="method",
    )
    selected_actions = _normalize_selection(
        action_ids,
        available=set(actions_by_id),
        limit=MAX_ACTION_CANDIDATES,
        kind="action",
    )
    if not selected_actions:
        raise CapabilityCompositionError("candidate.action.selection_empty")

    method_bindings = tuple(
        MethodCandidateBindingV1(
            candidate_id=f"M{index:02d}",
            primitive=methods_by_id[method_id],
            binding_sha256="0" * 64,
        ).with_computed_sha256()
        for index, method_id in enumerate(selected_methods, start=1)
    )
    action_bindings = tuple(
        ActionCandidateBindingV1(
            candidate_id=f"A{index:02d}",
            primitive=actions_by_id[action_id],
            source_revision=derive_action_source_revision(actions_by_id[action_id]),
            binding_sha256="0" * 64,
        ).with_computed_sha256()
        for index, action_id in enumerate(selected_actions, start=1)
    )
    snapshot = CompositionCandidateSnapshotV1(
        schema="tiangong.composition-candidates.v1",
        tool_world_sha256=tool_world.snapshot_sha256,
        method_world_sha256=method_world.snapshot_sha256,
        method_candidates=method_bindings,
        action_candidates=action_bindings,
        candidate_snapshot_sha256="0" * 64,
    )
    return replace(
        snapshot, candidate_snapshot_sha256=snapshot.computed_sha256()
    )


def validate_registry_binding(
    snapshot: CompositionCandidateSnapshotV1,
    registry: ActionRegistrySnapshot,
) -> None:
    """Check candidate action identity against the existing Action Registry."""

    if not snapshot.has_valid_sha256():
        raise CapabilityCompositionError("candidate.snapshot.hash_invalid")
    if not registry.has_valid_sha256():
        raise CapabilityCompositionError("candidate.registry.hash_invalid")
    permissions = {item.action_id: item for item in registry.permissions}
    for item in snapshot.action_candidates:
        permission = permissions.get(item.primitive.action_id)
        if (
            permission is None
            or permission.action_version != item.primitive.action_version
            or permission.source_manifest_sha256
            != item.primitive.action_manifest_sha256
            or permission.effective_risk != item.primitive.risk_floor
            or permission.effect != item.primitive.effect_class.removeprefix("effect:")
        ):
            raise CapabilityCompositionError(
                "candidate.registry.binding_mismatch", item.primitive.action_id
            )


__all__ = [
    "ActionCandidateBindingV1",
    "CapabilityCompositionError",
    "CompositionCandidateSnapshotV1",
    "CompositionCompileContextV1",
    "MAX_ACTION_CANDIDATES",
    "MAX_METHOD_CANDIDATES",
    "MethodCandidateBindingV1",
    "build_candidate_snapshot",
    "derive_action_source_revision",
    "validate_registry_binding",
]
