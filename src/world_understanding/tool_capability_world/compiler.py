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
from typing import Any, Mapping

from contracts import ActionRegistrySnapshot, canonical_sha256
from contracts.capability_composition import (
    SourceRevisionRefV1,
    SourceSpanRefV1,
    ToolSourcePrimitiveV1,
)


class ToolCapabilityWorldError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ToolCapabilityRelationV1:
    relation_type: str
    source_ref: str
    target_ref: str

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

    def payload(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "source_manifest_sha256": self.source_manifest_sha256,
            "action_registry_sha256": self.action_registry_sha256,
            "primitives": [item.model_dump(mode="json") for item in self.primitives],
            "relations": [item.to_dict() for item in self.relations],
        }

    def has_valid_sha256(self) -> bool:
        return self.snapshot_sha256 == canonical_sha256(self.payload())


def _implementation_refs(source: SourceRevisionRefV1) -> tuple[SourceSpanRefV1, ...]:
    if source.source_spans:
        return source.source_spans
    return tuple(SourceSpanRefV1(path=path) for path in source.source_files)


def _primitive_sha256_payload(primitive: ToolSourcePrimitiveV1) -> dict[str, Any]:
    return primitive.model_dump(mode="json", exclude={"descriptor_sha256"})


def _build_primitive(
    *,
    action_id: str,
    raw: Mapping[str, Any],
    permission: Any,
    source: SourceRevisionRefV1,
    argument_schema_sha256: str,
    result_schema_sha256: str,
    provider_component_id: str,
) -> ToolSourcePrimitiveV1:
    effect = str(permission.effect)
    idempotency = "IDEMPOTENT" if effect in {"read", "verify"} else "UNKNOWN"
    determinism = "DETERMINISTIC" if effect == "verify" else "BOUNDED_NONDETERMINISTIC"
    resources = [str(permission.path_policy)]
    if permission.allow_absolute_paths:
        resources.append("absolute_paths")
    if permission.allow_shell:
        resources.append("shell")
    if permission.allow_python:
        resources.append("python")

    primitive = ToolSourcePrimitiveV1(
        source_primitive_id=f"tool-source:{action_id}",
        action_id=action_id,
        action_version=str(permission.action_version),
        provider_component_id=provider_component_id,
        implementation_refs=_implementation_refs(source),
        implementation_hashes=(source.source_sha256,),
        action_manifest_sha256=permission.source_manifest_sha256,
        argument_schema_sha256=argument_schema_sha256,
        result_schema_sha256=result_schema_sha256,
        consumes=(),
        produces=(f"effect:{effect}",),
        effect_class=f"effect:{effect}",
        side_effects=tuple(str(item) for item in permission.allowed_side_effects),
        risk_floor=str(permission.effective_risk),
        idempotency=idempotency,
        determinism_class=determinism,
        resource_scope=tuple(sorted(set(resources))),
        read_set_descriptor=("resource:read",) if effect in {"read", "verify"} else (),
        write_set_descriptor=("resource:write",) if effect in {"create", "write", "update", "execute"} else (),
        evidence_contract=(),
        verifier_refs=(),
        failure_taxonomy=("unavailable", "runtime_failure", "verification_failure"),
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
    provider_component_id: str = "omni-body",
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
    if validation.get("ok") is not True:
        raise ToolCapabilityWorldError("capability manifest is not healthy")

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
            if isinstance(raw, Mapping) and raw.get("executable") is True
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
            raise ToolCapabilityWorldError(f"missing deterministic source/schema binding: {action_id}")
        if source.source_kind != "TOOL_ACTION" or source.semantic_id != action_id:
            raise ToolCapabilityWorldError(f"source revision identity mismatch: {action_id}")
        if source.manifest_sha256 not in {None, manifest_sha256}:
            raise ToolCapabilityWorldError(f"source revision manifest mismatch: {action_id}")

        primitive = _build_primitive(
            action_id=action_id,
            raw=raw,
            permission=permission,
            source=source,
            argument_schema_sha256=arg_hash,
            result_schema_sha256=result_hash,
            provider_component_id=provider_component_id,
        )
        primitives.append(primitive)

        primitive_ref = primitive.source_primitive_id
        relations.add(("COMPILES_TO", primitive_ref, f"action:{action_id}"))
        relations.add(("PROVIDED_BY", primitive_ref, f"provider:{provider_component_id}"))
        relations.add(("AVAILABLE_IN", primitive_ref, f"manifest:{manifest_sha256}"))
        for path in source.source_files:
            relations.add(("IMPLEMENTED_BY", primitive_ref, f"source:{path}"))
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
