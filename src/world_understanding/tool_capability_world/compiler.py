"""Compile a non-authorizing Tool Capability World snapshot.

Authority boundary:
- execution truth comes from the existing capability manifest;
- permission/risk truth comes from the existing ActionRegistrySnapshot;
- source truth comes from supplied SourceRevisionRefV1 records;
- this module only projects those facts into capability semantics.

It deliberately does not create a Tool Registry, Runtime, Grant, Ticket,
WorldState store, or executable route.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
import re
from typing import Any, Mapping

from contracts import ActionRegistrySnapshot, canonical_sha256
from contracts.capability_composition import (
    SourceRevisionRefV1,
    SourceSpanRefV1,
    ToolSourcePrimitiveV1,
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CANONICAL_PROVIDER_COMPONENT_ID = "omni-body"
_P2_RELATION_TYPES = frozenset(
    {
        "COMPILES_TO",
        "IMPLEMENTED_BY",
        "PROVIDED_BY",
        "AVAILABLE_IN",
        "ALIASES",
        "READS",
        "WRITES",
        "PRODUCES",
    }
)


class ToolCapabilityWorldError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ToolCapabilityRelationV1:
    relation_type: str
    source_ref: str
    target_ref: str

    def __post_init__(self) -> None:
        if self.relation_type not in _P2_RELATION_TYPES:
            raise ToolCapabilityWorldError("P2 relation type is not mechanically authorized")
        if not self.source_ref or not self.target_ref:
            raise ToolCapabilityWorldError("P2 relation endpoints must be non-empty")

    def to_dict(self) -> dict[str, str]:
        return {
            "relation_type": self.relation_type,
            "source_ref": self.source_ref,
            "target_ref": self.target_ref,
        }


@dataclass(frozen=True, slots=True)
class ToolCapabilityWorldSnapshotV1:
    schema: str
    source_manifest_sha256: str
    action_registry_sha256: str
    primitives: tuple[ToolSourcePrimitiveV1, ...]
    relations: tuple[ToolCapabilityRelationV1, ...]
    snapshot_sha256: str
    may_authorize: bool = False
    may_execute: bool = False

    def __post_init__(self) -> None:
        if self.schema != "tiangong.tool-capability-world.v1":
            raise ToolCapabilityWorldError("unsupported Tool Capability World snapshot schema")
        if self.may_authorize or self.may_execute:
            raise ToolCapabilityWorldError("Tool Capability World is non-authorizing and non-executing")
        primitive_ids = tuple(item.action_id for item in self.primitives)
        if primitive_ids != tuple(sorted(set(primitive_ids))):
            raise ToolCapabilityWorldError("Tool Capability World primitives must be sorted and unique")
        relation_keys = tuple(
            (item.relation_type, item.source_ref, item.target_ref) for item in self.relations
        )
        if relation_keys != tuple(sorted(set(relation_keys))):
            raise ToolCapabilityWorldError("Tool Capability World relations must be sorted and unique")

    def payload(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "source_manifest_sha256": self.source_manifest_sha256,
            "action_registry_sha256": self.action_registry_sha256,
            "may_authorize": self.may_authorize,
            "may_execute": self.may_execute,
            "primitives": [item.model_dump(mode="json") for item in self.primitives],
            "relations": [item.to_dict() for item in self.relations],
        }

    def has_valid_sha256(self) -> bool:
        return self.snapshot_sha256 == canonical_sha256(self.payload())


def _validated_source_files(
    source: SourceRevisionRefV1,
    *,
    action_id: str,
) -> tuple[str, ...]:
    files = tuple(source.source_files)
    if files != tuple(sorted(set(files))):
        # SourceRevisionRef is an immutable identity record; accepting multiple
        # byte representations for the same file set would make replay hashes
        # caller-order dependent.
        raise ToolCapabilityWorldError(
            f"source revision files must be sorted and unique: {action_id}"
        )
    for path in files:
        posix = PurePosixPath(path)
        if (
            not path
            or path != path.strip()
            or str(posix) != path
            or "\\" in path
            or posix.is_absolute()
            or ".." in posix.parts
            or "." in posix.parts
        ):
            raise ToolCapabilityWorldError(
                f"source revision contains an unsafe repository path: {action_id}"
            )
    return files


def _implementation_refs(
    source: SourceRevisionRefV1,
    *,
    action_id: str,
) -> tuple[SourceSpanRefV1, ...]:
    files = _validated_source_files(source, action_id=action_id)
    refs = source.source_spans or tuple(SourceSpanRefV1(path=path) for path in files)
    keys = tuple((item.path, item.start_line or 0, item.end_line or 0) for item in refs)
    if (
        len(keys) != len(set(keys))
        or any(item.path not in files for item in refs)
    ):
        raise ToolCapabilityWorldError(
            f"source revision implementation refs are invalid: {action_id}"
        )
    return tuple(
        sorted(refs, key=lambda item: (item.path, item.start_line or 0, item.end_line or 0))
    )


def _primitive_sha256_payload(primitive: ToolSourcePrimitiveV1) -> dict[str, Any]:
    return primitive.model_dump(mode="json", exclude={"descriptor_sha256"})


def _require_sha256(value: object, *, field: str, action_id: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ToolCapabilityWorldError(f"invalid {field} binding: {action_id}")
    return value


def _build_primitive(
    *,
    action_id: str,
    permission: Any,
    source: SourceRevisionRefV1,
    argument_schema_sha256: str,
    result_schema_sha256: str,
) -> ToolSourcePrimitiveV1:
    effect = str(permission.effect)
    resources = [str(permission.path_policy)]
    if permission.allow_absolute_paths:
        resources.append("absolute_paths")
    if permission.allow_shell:
        resources.append("shell")
    if permission.allow_python:
        resources.append("python")

    # P2 has no independent evidence proving operational idempotency or
    # determinism. Preserve UNKNOWN/conservative semantics instead of guessing
    # from effect labels such as read/verify.
    primitive = ToolSourcePrimitiveV1(
        source_primitive_id=f"tool-source:{action_id}",
        action_id=action_id,
        action_version=str(permission.action_version),
        provider_component_id=_CANONICAL_PROVIDER_COMPONENT_ID,
        implementation_refs=_implementation_refs(source, action_id=action_id),
        implementation_hashes=(source.source_sha256,),
        action_manifest_sha256=permission.source_manifest_sha256,
        argument_schema_sha256=argument_schema_sha256,
        result_schema_sha256=result_schema_sha256,
        consumes=(),
        produces=(f"effect:{effect}",),
        effect_class=f"effect:{effect}",
        side_effects=tuple(str(item) for item in permission.allowed_side_effects),
        risk_floor=str(permission.effective_risk),
        idempotency="UNKNOWN",
        determinism_class="NONDETERMINISTIC",
        resource_scope=tuple(sorted(set(resources))),
        read_set_descriptor=("resource:read",) if effect in {"read", "verify"} else (),
        write_set_descriptor=("resource:write",)
        if effect in {"create", "write", "update", "execute"}
        else (),
        evidence_contract=(),
        verifier_refs=(),
        failure_taxonomy=("runtime_failure", "unavailable", "verification_failure"),
        availability="AVAILABLE",
        descriptor_sha256="0" * 64,
    )
    return primitive.model_copy(
        update={"descriptor_sha256": canonical_sha256(_primitive_sha256_payload(primitive))}
    )


def compile_tool_capability_world(
    manifest: Mapping[str, Any],
    registry: ActionRegistrySnapshot,
    *,
    source_revisions: Mapping[str, SourceRevisionRefV1],
    argument_schema_hashes: Mapping[str, str],
    result_schema_hashes: Mapping[str, str],
) -> ToolCapabilityWorldSnapshotV1:
    """Project the existing execution authorities into deterministic semantics.

    All executable actions must exist in both the manifest and Action Registry.
    Source/schema bindings must be complete. Missing authority evidence is a
    hard error rather than a guessed capability.
    """

    if manifest.get("schema") != "tiangong.v3.capability_manifest.v1":
        raise ToolCapabilityWorldError("unsupported capability manifest")
    capabilities = manifest.get("capabilities")
    validation = manifest.get("validation")
    if not isinstance(capabilities, Mapping) or not isinstance(validation, Mapping):
        raise ToolCapabilityWorldError("capability manifest is incomplete")
    if validation.get("ok") is not True or validation.get("executable_without_route") != []:
        raise ToolCapabilityWorldError("capability manifest is not healthy")
    if validation.get("source_hash") != manifest.get("source_hash"):
        raise ToolCapabilityWorldError("capability manifest validation hash is stale")

    manifest_sha256 = canonical_sha256(dict(manifest))
    if registry.source_manifest_sha256 != manifest_sha256:
        raise ToolCapabilityWorldError("registry is not bound to the supplied manifest")
    if not registry.has_valid_sha256():
        raise ToolCapabilityWorldError("action registry hash is invalid")

    permissions = {item.action_id: item for item in registry.permissions}
    executable_ids = tuple(
        sorted(
            action_id
            for action_id, raw in capabilities.items()
            if isinstance(action_id, str)
            and isinstance(raw, Mapping)
            and raw.get("executable") is True
        )
    )
    if executable_ids != tuple(sorted(permissions)):
        raise ToolCapabilityWorldError("manifest executable set differs from action registry")

    primitives: list[ToolSourcePrimitiveV1] = []
    relations: set[tuple[str, str, str]] = set()
    for action_id in executable_ids:
        raw = capabilities[action_id]
        permission = permissions[action_id]
        source = source_revisions.get(action_id)
        arg_hash = argument_schema_hashes.get(action_id)
        result_hash = result_schema_hashes.get(action_id)
        if source is None or arg_hash is None or result_hash is None:
            raise ToolCapabilityWorldError(
                f"missing deterministic source/schema binding: {action_id}"
            )
        if (
            source.source_kind != "TOOL_ACTION"
            or source.semantic_id != action_id
            or source.version != str(permission.action_version)
        ):
            raise ToolCapabilityWorldError(
                f"source revision identity/version mismatch: {action_id}"
            )
        if source.manifest_sha256 != manifest_sha256:
            raise ToolCapabilityWorldError(
                f"source revision manifest mismatch: {action_id}"
            )

        primitive = _build_primitive(
            action_id=action_id,
            permission=permission,
            source=source,
            argument_schema_sha256=_require_sha256(
                arg_hash, field="argument schema", action_id=action_id
            ),
            result_schema_sha256=_require_sha256(
                result_hash, field="result schema", action_id=action_id
            ),
        )
        primitives.append(primitive)

        primitive_ref = primitive.source_primitive_id
        relations.add(("COMPILES_TO", primitive_ref, f"action:{action_id}"))
        relations.add(
            ("PROVIDED_BY", primitive_ref, f"provider:{_CANONICAL_PROVIDER_COMPONENT_ID}")
        )
        relations.add(("AVAILABLE_IN", primitive_ref, f"manifest:{manifest_sha256}"))
        for implementation_ref in primitive.implementation_refs:
            relations.add(
                ("IMPLEMENTED_BY", primitive_ref, f"source:{implementation_ref.path}")
            )
        alias_to = str(raw.get("alias_to") or "").strip()
        if alias_to:
            relations.add(("ALIASES", f"action:{action_id}", f"action:{alias_to}"))
        if permission.effect in {"read", "verify"}:
            relations.add(("READS", primitive_ref, "resource:read"))
        else:
            relations.add(("WRITES", primitive_ref, "resource:write"))
        relations.add(("PRODUCES", primitive_ref, f"effect:{permission.effect}"))

    ordered_relations = tuple(
        ToolCapabilityRelationV1(*item) for item in sorted(relations)
    )
    snapshot = ToolCapabilityWorldSnapshotV1(
        schema="tiangong.tool-capability-world.v1",
        source_manifest_sha256=manifest_sha256,
        action_registry_sha256=registry.registry_sha256,
        primitives=tuple(primitives),
        relations=ordered_relations,
        snapshot_sha256="0" * 64,
    )
    return ToolCapabilityWorldSnapshotV1(
        schema=snapshot.schema,
        source_manifest_sha256=snapshot.source_manifest_sha256,
        action_registry_sha256=snapshot.action_registry_sha256,
        primitives=snapshot.primitives,
        relations=snapshot.relations,
        snapshot_sha256=canonical_sha256(snapshot.payload()),
    )
