"""P9 Method Source lifecycle planning for the non-authorizing Skill Method World.

This module validates and compiles add/update/remove candidates into one
content-addressed lifecycle plan.  It never publishes a World revision, writes
Memory, mints execution authority, or changes the current running plan.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import PurePosixPath
from typing import Any, Literal
import re

from contracts import canonical_sha256
from contracts.capability_composition import SkillSourcePrimitiveV1

from .compiler import _validate_prefixed_values, computed_skill_method_descriptor_sha256
from .models import SkillMethodWorldError, SkillMethodWorldSnapshotV1


_METHOD_SOURCE_LIFECYCLE_SCHEMA = "tiangong.skill-method-source-lifecycle.v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
MethodSourceOperation = Literal["ADD", "UPDATE", "REMOVE"]


def _validate_method_source_primitive_structure(primitive: SkillSourcePrimitiveV1) -> None:
    ref = primitive.source_ref
    if (
        ref.source_kind != "SKILL_METHOD"
        or ref.semantic_id != primitive.method_id
        or ref.version != primitive.version
        or ref.manifest_sha256 is not None
        or primitive.source_sha256 != ref.source_sha256
    ):
        raise SkillMethodWorldError("method source identity is invalid")
    files = tuple(ref.source_files)
    if files != tuple(sorted(set(files))):
        raise SkillMethodWorldError("method source files must be sorted and unique")
    for source_path in files:
        posix = PurePosixPath(source_path)
        if (
            posix.is_absolute()
            or source_path != source_path.strip()
            or str(posix) != source_path
            or "\\" in source_path
            or ".." in posix.parts
        ):
            raise SkillMethodWorldError("method source path is unsafe")
    spans = tuple((item.path, item.start_line or 0, item.end_line or 0) for item in ref.source_spans)
    if (
        spans != tuple(sorted(set(spans)))
        or any(item.path not in files for item in ref.source_spans)
    ):
        raise SkillMethodWorldError("method source spans are invalid")
    if (
        primitive.descriptor_sha256 != computed_skill_method_descriptor_sha256(primitive)
        or ref.descriptor_sha256 != primitive.descriptor_sha256
    ):
        raise SkillMethodWorldError("method source descriptor hash is invalid")
    _validate_prefixed_values(primitive)


@dataclass(frozen=True, slots=True)
class MethodSourceCandidateV1:
    candidate_id: str
    operation: MethodSourceOperation
    base_snapshot_sha256: str
    method_id: str
    base_descriptor_sha256: str | None
    primitive: SkillSourcePrimitiveV1 | None
    simulation_evidence_sha256: str
    candidate_sha256: str
    may_authorize: bool = False
    may_execute: bool = False

    def __post_init__(self) -> None:
        if self.may_authorize or self.may_execute:
            raise SkillMethodWorldError("method source candidate is non-authorizing")
        if not self.candidate_id or not self.method_id:
            raise SkillMethodWorldError("method source candidate identity is incomplete")
        if _SHA256.fullmatch(self.base_snapshot_sha256) is None:
            raise SkillMethodWorldError("method source candidate base snapshot hash is invalid")
        if _SHA256.fullmatch(self.simulation_evidence_sha256) is None:
            raise SkillMethodWorldError("method source candidate simulation evidence hash is invalid")
        if self.base_descriptor_sha256 is not None and _SHA256.fullmatch(self.base_descriptor_sha256) is None:
            raise SkillMethodWorldError("method source candidate base descriptor hash is invalid")
        if self.operation == "ADD":
            if self.primitive is None or self.base_descriptor_sha256 is not None:
                raise SkillMethodWorldError("ADD requires a primitive and no previous descriptor")
        elif self.operation == "UPDATE":
            if self.primitive is None or self.base_descriptor_sha256 is None:
                raise SkillMethodWorldError("UPDATE requires a primitive and previous descriptor")
        elif self.operation == "REMOVE":
            if self.primitive is not None or self.base_descriptor_sha256 is None:
                raise SkillMethodWorldError("REMOVE requires the previous descriptor and no primitive")
        else:  # pragma: no cover - Literal is a typing aid, runtime remains fail-closed.
            raise SkillMethodWorldError("unsupported method source lifecycle operation")
        if self.primitive is not None:
            if self.primitive.method_id != self.method_id:
                raise SkillMethodWorldError("method source candidate method identity drifted")
            _validate_method_source_primitive_structure(self.primitive)

    def payload(self) -> dict[str, Any]:
        return {
            "schema": _METHOD_SOURCE_LIFECYCLE_SCHEMA,
            "candidate_id": self.candidate_id,
            "operation": self.operation,
            "base_snapshot_sha256": self.base_snapshot_sha256,
            "method_id": self.method_id,
            "base_descriptor_sha256": self.base_descriptor_sha256,
            "primitive": None if self.primitive is None else self.primitive.model_dump(mode="json"),
            "simulation_evidence_sha256": self.simulation_evidence_sha256,
            "may_authorize": self.may_authorize,
            "may_execute": self.may_execute,
        }

    def computed_sha256(self) -> str:
        return canonical_sha256(self.payload())

    def has_valid_sha256(self) -> bool:
        return self.candidate_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> "MethodSourceCandidateV1":
        return replace(self, candidate_sha256=self.computed_sha256())


@dataclass(frozen=True, slots=True)
class MethodSourceChangeV1:
    operation: MethodSourceOperation
    method_id: str
    previous_version: str | None
    next_version: str | None
    previous_source_sha256: str | None
    next_source_sha256: str | None
    previous_descriptor_sha256: str | None
    next_descriptor_sha256: str | None
    invalidation_refs: tuple[str, ...]
    change_sha256: str

    def payload(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "method_id": self.method_id,
            "previous_version": self.previous_version,
            "next_version": self.next_version,
            "previous_source_sha256": self.previous_source_sha256,
            "next_source_sha256": self.next_source_sha256,
            "previous_descriptor_sha256": self.previous_descriptor_sha256,
            "next_descriptor_sha256": self.next_descriptor_sha256,
            "invalidation_refs": list(self.invalidation_refs),
        }

    def computed_sha256(self) -> str:
        return canonical_sha256(self.payload())

    def has_valid_sha256(self) -> bool:
        return self.change_sha256 == self.computed_sha256()


@dataclass(frozen=True, slots=True)
class MethodSourceLifecyclePlanV1:
    schema: str
    base_snapshot_sha256: str
    candidate_sha256s: tuple[str, ...]
    changes: tuple[MethodSourceChangeV1, ...]
    next_primitives: tuple[SkillSourcePrimitiveV1, ...]
    next_method_sources_sha256: str
    invalidation_refs: tuple[str, ...]
    plan_sha256: str
    may_publish: bool = False
    may_authorize: bool = False
    may_execute: bool = False

    def __post_init__(self) -> None:
        if self.schema != _METHOD_SOURCE_LIFECYCLE_SCHEMA:
            raise SkillMethodWorldError("unsupported method source lifecycle schema")
        if self.may_publish or self.may_authorize or self.may_execute:
            raise SkillMethodWorldError("method source lifecycle plan has no publication or execution authority")
        if _SHA256.fullmatch(self.base_snapshot_sha256) is None:
            raise SkillMethodWorldError("method source lifecycle base hash is invalid")
        if not self.candidate_sha256s or self.candidate_sha256s != tuple(sorted(set(self.candidate_sha256s))):
            raise SkillMethodWorldError("method source candidate hashes must be sorted and unique")
        if any(not item.has_valid_sha256() for item in self.changes):
            raise SkillMethodWorldError("method source change hash is invalid")
        change_ids = tuple(item.method_id for item in self.changes)
        if change_ids != tuple(sorted(set(change_ids))):
            raise SkillMethodWorldError("method source changes must be sorted and unique")
        primitive_ids = tuple(item.method_id for item in self.next_primitives)
        if primitive_ids != tuple(sorted(set(primitive_ids))):
            raise SkillMethodWorldError("next method sources must be sorted and unique")
        if self.invalidation_refs != tuple(sorted(set(self.invalidation_refs))):
            raise SkillMethodWorldError("method source invalidation refs must be sorted and unique")

    def payload(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "base_snapshot_sha256": self.base_snapshot_sha256,
            "candidate_sha256s": list(self.candidate_sha256s),
            "changes": [{**item.payload(), "change_sha256": item.change_sha256} for item in self.changes],
            "next_primitives": [item.model_dump(mode="json") for item in self.next_primitives],
            "next_method_sources_sha256": self.next_method_sources_sha256,
            "invalidation_refs": list(self.invalidation_refs),
            "may_publish": self.may_publish,
            "may_authorize": self.may_authorize,
            "may_execute": self.may_execute,
        }

    def computed_sha256(self) -> str:
        return canonical_sha256(self.payload())

    def has_valid_sha256(self) -> bool:
        return self.plan_sha256 == self.computed_sha256()


def _invalidation_refs(method_id: str) -> tuple[str, ...]:
    return tuple(sorted({
        f"method:{method_id}",
        "skill-method-world:current",
        "capability-composition:method-candidates",
        "world-context:method-candidates",
    }))


def _method_sources_sha256(primitives: tuple[SkillSourcePrimitiveV1, ...]) -> str:
    return canonical_sha256({
        "domain": "tiangong.skill-method-sources.v1",
        "primitives": [item.model_dump(mode="json") for item in primitives],
    })


def compile_method_source_lifecycle(
    base_snapshot: SkillMethodWorldSnapshotV1,
    candidates: tuple[MethodSourceCandidateV1, ...],
) -> MethodSourceLifecyclePlanV1:
    """Compile deterministic add/update/remove intent without publishing it.

    Simulation evidence remains an externally verified prerequisite for a later
    publication boundary.  This function only binds its digest and emits
    invalidation intent; it never treats the candidate as the current World.
    """
    if not base_snapshot.has_valid_sha256():
        raise SkillMethodWorldError("base Skill Method World snapshot hash is invalid")
    if not candidates:
        raise SkillMethodWorldError("method source lifecycle requires at least one candidate")
    if any(not item.has_valid_sha256() for item in candidates):
        raise SkillMethodWorldError("method source candidate hash is invalid")
    method_ids = tuple(item.method_id for item in candidates)
    if len(method_ids) != len(set(method_ids)):
        raise SkillMethodWorldError("one lifecycle plan may change each method at most once")
    if any(item.base_snapshot_sha256 != base_snapshot.snapshot_sha256 for item in candidates):
        raise SkillMethodWorldError("method source candidate targets a stale World snapshot")

    current = {item.method_id: item for item in base_snapshot.primitives}
    changes: list[MethodSourceChangeV1] = []
    invalidation: set[str] = set()

    for candidate in sorted(candidates, key=lambda item: item.method_id):
        old = current.get(candidate.method_id)
        new = candidate.primitive
        if candidate.operation == "ADD":
            if old is not None:
                raise SkillMethodWorldError("ADD cannot replace an existing method source")
            assert new is not None
            current[candidate.method_id] = new
        elif candidate.operation == "UPDATE":
            if old is None:
                raise SkillMethodWorldError("UPDATE requires an existing method source")
            if old.descriptor_sha256 != candidate.base_descriptor_sha256:
                raise SkillMethodWorldError("UPDATE previous descriptor does not match current World")
            assert new is not None
            if new.version == old.version:
                raise SkillMethodWorldError("UPDATE must advance the method source version")
            if new.source_sha256 == old.source_sha256:
                raise SkillMethodWorldError("UPDATE must bind a new method source revision")
            if new.descriptor_sha256 == old.descriptor_sha256:
                raise SkillMethodWorldError("UPDATE must change the method descriptor")
            current[candidate.method_id] = new
        else:
            if old is None:
                raise SkillMethodWorldError("REMOVE requires an existing method source")
            if old.descriptor_sha256 != candidate.base_descriptor_sha256:
                raise SkillMethodWorldError("REMOVE previous descriptor does not match current World")
            del current[candidate.method_id]

        refs = _invalidation_refs(candidate.method_id)
        invalidation.update(refs)
        change = MethodSourceChangeV1(
            operation=candidate.operation,
            method_id=candidate.method_id,
            previous_version=None if old is None else old.version,
            next_version=None if new is None else new.version,
            previous_source_sha256=None if old is None else old.source_sha256,
            next_source_sha256=None if new is None else new.source_sha256,
            previous_descriptor_sha256=None if old is None else old.descriptor_sha256,
            next_descriptor_sha256=None if new is None else new.descriptor_sha256,
            invalidation_refs=refs,
            change_sha256="0" * 64,
        )
        changes.append(replace(change, change_sha256=change.computed_sha256()))

    next_primitives = tuple(sorted(current.values(), key=lambda item: item.method_id))
    if not next_primitives:
        raise SkillMethodWorldError("method source lifecycle cannot remove the entire Method World")
    plan = MethodSourceLifecyclePlanV1(
        schema=_METHOD_SOURCE_LIFECYCLE_SCHEMA,
        base_snapshot_sha256=base_snapshot.snapshot_sha256,
        candidate_sha256s=tuple(sorted(item.candidate_sha256 for item in candidates)),
        changes=tuple(changes),
        next_primitives=next_primitives,
        next_method_sources_sha256=_method_sources_sha256(next_primitives),
        invalidation_refs=tuple(sorted(invalidation)),
        plan_sha256="0" * 64,
    )
    return replace(plan, plan_sha256=plan.computed_sha256())


__all__ = [
    "MethodSourceCandidateV1",
    "MethodSourceChangeV1",
    "MethodSourceLifecyclePlanV1",
    "MethodSourceOperation",
    "compile_method_source_lifecycle",
]
