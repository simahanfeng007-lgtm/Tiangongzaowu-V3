"""Shared release-pinned capability loading and execution-manifest compilation.

This is the existing implementation extracted from skill_selection in P12 R1B,
not another registry, planner or execution authority. The legacy module re-exports
these same objects for import compatibility. SkillSelectionError retains its public
name and messages; neither this module nor its import closure needs the selector.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from contracts import (
    ActionRegistrySnapshot,
    CapabilityAction,
    CapabilityManifest,
    canonical_sha256,
)
from .action_registry import (
    ActionSchemaCatalog,
    LoadedActionAuthority,
    ResolvedActionSchema,
    compile_action_authority,
)


class SkillSelectionError(RuntimeError):
    pass


@dataclass(frozen=True)
class LoadedModelCapabilityManifest:
    manifest: CapabilityManifest
    source_sha256: str
    executable_count: int
    action_authority: LoadedActionAuthority


def _strict_json_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise SkillSelectionError("Skill index contains a duplicate JSON key")
        result[key] = value
    return result


def _routing_side_effects(effect: str) -> tuple[str, ...]:
    if effect in {"read", "verify", "inspect", "list"}:
        return ("read",)
    if effect in {"write", "create", "update"}:
        return ("local_write", "read")
    return ("external_send", "external_write", "local_write", "read")


def load_model_capability_manifest(
    path: Path,
    *,
    expected_sha256: str,
    component_manifest_hash: str,
    generated_at_ms: int,
) -> LoadedModelCapabilityManifest:
    """Load the pinned executable action surface used for Skill compatibility.

    The gateway execution ticket intentionally exposes only one compatibility
    action.  Reusing that narrow ticket manifest for Skill routing made every
    real Skill look incompatible.  This loader creates a separate, read-only
    routing view from the backend's release-pinned capability manifest.
    """

    if not path.is_absolute() or not path.is_file() or path.is_symlink() or path.parent.is_symlink():
        raise SkillSelectionError("model capability manifest path is missing or unsafe")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise ValueError("expected model capability digest is invalid")
    data = path.read_bytes()
    if not data or len(data) > 8 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != expected_sha256:
        raise SkillSelectionError("model capability manifest digest does not match the pinned release")
    try:
        payload = json.loads(
            data.decode("utf-8", errors="strict"),
            object_pairs_hook=_strict_json_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(
                SkillSelectionError("model capability manifest contains a non-finite number")
            ),
        )
    except SkillSelectionError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SkillSelectionError("model capability manifest is not strict UTF-8 JSON") from exc
    expected_root = {
        "capabilities",
        "executable",
        "schema",
        "source_hash",
        "total",
        "unavailable",
        "validation",
    }
    if (
        not isinstance(payload, dict)
        or set(payload) not in (expected_root, expected_root | {"source_inputs_sha256"})
        or payload.get("schema") != "tiangong.v3.capability_manifest.v1"
        or not isinstance(payload.get("capabilities"), dict)
        or not isinstance(payload.get("validation"), dict)
        or payload["validation"].get("ok") is not True
        or payload["validation"].get("source_hash") != payload.get("source_hash")
    ):
        raise SkillSelectionError("model capability manifest schema or validation is invalid")
    # P8 extends the existing release format with a source-input identity.
    # Its presence is not approval: the complete bytes are still release-pinned
    # above, and both routing and execution authorities use this ONE parsed
    # document. Legacy releases remain loadable without inventing a source pin.
    # Reject explicit null/invalid values and all other root extensions.
    if "source_inputs_sha256" in payload and (
        not isinstance(payload["source_inputs_sha256"], str)
        or re.fullmatch(r"[0-9a-f]{64}", payload["source_inputs_sha256"]) is None
    ):
        raise SkillSelectionError("model capability source input revision is invalid")
    capabilities = payload["capabilities"]
    executable_count = sum(
        1 for item in capabilities.values() if isinstance(item, dict) and item.get("executable") is True
    )
    if (
        isinstance(payload.get("total"), bool)
        or payload.get("total") != len(capabilities)
        or isinstance(payload.get("executable"), bool)
        or payload.get("executable") != executable_count
        or payload.get("unavailable") != len(capabilities) - executable_count
        or executable_count < 1
    ):
        raise SkillSelectionError("model capability manifest counts are invalid")

    try:
        action_authority = compile_action_authority(
            payload,
            generated_at_ms=generated_at_ms,
        )
    except (TypeError, ValueError) as exc:
        raise SkillSelectionError(
            "model capability schema authority is invalid"
        ) from exc

    actions: list[CapabilityAction] = []
    for action_id, raw in capabilities.items():
        if not isinstance(action_id, str) or not isinstance(raw, dict) or raw.get("id") != action_id:
            raise SkillSelectionError("model capability identity is invalid")
        if raw.get("executable") is not True:
            continue
        risk = str(raw.get("risk") or "")
        if risk not in {"A0", "A1", "A2", "A3", "A4", "A5"}:
            raise SkillSelectionError("model capability risk class is invalid")
        effect = str(raw.get("effect") or "execute")
        try:
            resolved_schema = action_authority.schema_catalog.resolve(
                action_id,
                "omni-registry-v1",
            )
        except (TypeError, ValueError) as exc:
            raise SkillSelectionError(
                "model capability schema binding is invalid"
            ) from exc
        from capability_dictionary import load_dictionary
        readiness = load_dictionary().readiness(action_id)
        actions.append(
            CapabilityAction(
                action_id=action_id,
                version="runtime-capability-v1",
                provider_component_id="tiangong-backend",
                argument_schema_sha256=resolved_schema.argument_schema_sha256,
                result_schema_sha256=resolved_schema.result_schema_sha256,
                risk_class=risk,
                allowed_side_effects=_routing_side_effects(effect),
                idempotency_mode="effect_id_required",
                max_runtime_ms=3_600_000,
                max_output_bytes=536_870_912,
                max_tool_calls=10_000,
                available=readiness["ready"],
                unavailable_reason=None if readiness["ready"] else ";".join(readiness["reasons"]),
                model_visible=True,
            )
        )
    manifest = CapabilityManifest(
        manifest_id="omni-body-model-capabilities-v1",
        revision=1,
        generated_at_ms=generated_at_ms,
        component_manifest_hash=component_manifest_hash,
        actions=tuple(sorted(actions, key=lambda item: (item.action_id, item.version))),
        sha256="0" * 64,
    ).with_computed_sha256()
    return LoadedModelCapabilityManifest(
        manifest=manifest,
        source_sha256=expected_sha256,
        executable_count=executable_count,
        action_authority=action_authority,
    )


def compile_composition_execution_manifest(
    model_manifest: CapabilityManifest,
    registry: ActionRegistrySnapshot,
    schema_catalog: ActionSchemaCatalog,
    *,
    generated_at_ms: int | None = None,
) -> CapabilityManifest:
    """Join the model, permission, and schema views into an execution manifest.

    ``load_model_capability_manifest`` intentionally exposes model-facing
    action versions. Composition tickets, however, carry the current Action
    Registry permission version. Passing the model-facing projection directly
    to ``BackendClient`` therefore cannot authorize a real composition action.

    This compiler does not treat the raw source-manifest digest as a
    ``CapabilityManifest`` digest. It validates the contract digest on the
    supplied model view, joins every registry permission to exactly one model
    action and one current schema entry, then computes a new contract digest
    over the resulting execution view.
    """

    if not isinstance(model_manifest, CapabilityManifest):
        raise SkillSelectionError(
            "composition execution model capability manifest is invalid"
        )
    if not isinstance(registry, ActionRegistrySnapshot):
        raise SkillSelectionError(
            "composition execution action registry is invalid"
        )
    if not isinstance(schema_catalog, ActionSchemaCatalog):
        raise SkillSelectionError(
            "composition execution action schema catalog is invalid"
        )
    if generated_at_ms is not None and (
        type(generated_at_ms) is not int or generated_at_ms < 0
    ):
        raise ValueError("composition execution manifest generation time is invalid")

    # model_copy/model_construct can bypass Pydantic validators. Re-parse the
    # supplied contracts before trusting their ordering and nested invariants,
    # and independently verify their content-addressed digests.
    try:
        checked_model = CapabilityManifest.model_validate(
            model_manifest.model_dump(mode="python"), strict=True
        )
    except (TypeError, ValueError) as exc:
        raise SkillSelectionError(
            "composition execution model capability manifest is invalid"
        ) from exc
    if not checked_model.has_valid_sha256():
        raise SkillSelectionError(
            "composition execution model capability manifest digest is invalid"
        )

    try:
        checked_registry = ActionRegistrySnapshot.model_validate(
            registry.model_dump(mode="python"), strict=True
        )
    except (TypeError, ValueError) as exc:
        raise SkillSelectionError(
            "composition execution action registry is invalid"
        ) from exc
    if not checked_registry.has_valid_sha256():
        raise SkillSelectionError(
            "composition execution action registry digest is invalid"
        )

    try:
        catalog_valid = schema_catalog.has_valid_sha256()
    except (AttributeError, TypeError, ValueError) as exc:
        raise SkillSelectionError(
            "composition execution action schema catalog is invalid"
        ) from exc
    if not catalog_valid:
        raise SkillSelectionError(
            "composition execution action schema catalog digest is invalid"
        )
    if (
        not isinstance(schema_catalog.source_manifest_sha256, str)
        or not re.fullmatch(
            r"[0-9a-f]{64}", schema_catalog.source_manifest_sha256
        )
        or schema_catalog.source_manifest_sha256
        != checked_registry.source_manifest_sha256
    ):
        raise SkillSelectionError(
            "composition execution authority source manifest mismatch"
        )

    permissions = checked_registry.permissions
    permission_ids = tuple(item.action_id for item in permissions)
    if permission_ids != tuple(sorted(set(permission_ids))):
        raise SkillSelectionError(
            "composition execution action permissions are unordered or ambiguous"
        )

    model_actions: dict[str, CapabilityAction] = {}
    for action in checked_model.actions:
        if action.action_id in model_actions:
            raise SkillSelectionError(
                "composition execution model action identity is ambiguous"
            )
        model_actions[action.action_id] = action
    if set(model_actions) != set(permission_ids):
        raise SkillSelectionError(
            "composition execution model and permission coverage mismatch"
        )

    entries = schema_catalog.entries
    if not isinstance(entries, tuple) or not entries:
        raise SkillSelectionError(
            "composition execution action schema catalog is invalid"
        )
    if any(not isinstance(entry, ResolvedActionSchema) for entry in entries):
        raise SkillSelectionError(
            "composition execution action schema entry is invalid"
        )
    entry_ids = tuple(entry.action_id for entry in entries)
    if entry_ids != permission_ids:
        raise SkillSelectionError(
            "composition execution permission and schema coverage mismatch"
        )

    compiled_actions: list[CapabilityAction] = []
    for permission, entry in zip(permissions, entries, strict=True):
        if (
            entry.action_id != permission.action_id
            or entry.action_version != permission.action_version
            or entry.source_manifest_sha256
            != schema_catalog.source_manifest_sha256
            or entry.kind not in {"EXPLICIT", "OPAQUE"}
            or entry.result_schema_kind not in {"EXPLICIT", "OPAQUE"}
            or entry.value_schema_kind not in {"EXPLICIT", "OPAQUE"}
            or not isinstance(entry.argument_schema_sha256, str)
            or not re.fullmatch(
                r"[0-9a-f]{64}", entry.argument_schema_sha256
            )
            or not isinstance(entry.validator_source_sha256, str)
            or not re.fullmatch(
                r"[0-9a-f]{64}", entry.validator_source_sha256
            )
            or not isinstance(entry.result_schema_sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", entry.result_schema_sha256)
            or not isinstance(entry.result_validator_source_sha256, str)
            or not re.fullmatch(
                r"[0-9a-f]{64}", entry.result_validator_source_sha256
            )
            or not isinstance(entry.value_validator_source_sha256, str)
            or not re.fullmatch(
                r"[0-9a-f]{64}", entry.value_validator_source_sha256
            )
            or (entry.value_schema_kind == "EXPLICIT") != bool(entry.value_schemas)
            or entry.result_schema_kind != entry.value_schema_kind
        ):
            raise SkillSelectionError(
                "composition execution permission and schema binding mismatch"
            )
        try:
            schema_body = entry.body()
            result_schema_body = entry.result_body()
        except (AttributeError, TypeError, ValueError) as exc:
            raise SkillSelectionError(
                "composition execution action schema entry is invalid"
            ) from exc
        if (
            canonical_sha256(schema_body) != entry.argument_schema_sha256
            or schema_body.get("action") != entry.canonical_action_id
            or canonical_sha256(result_schema_body) != entry.result_schema_sha256
            or result_schema_body.get("action") != entry.canonical_action_id
            or result_schema_body.get("kind") != entry.result_schema_kind
        ):
            raise SkillSelectionError(
                "composition execution action schema body is invalid"
            )

        value_schema_ids = tuple(item.value_schema_id for item in entry.value_schemas)
        if value_schema_ids != tuple(sorted(set(value_schema_ids))):
            raise SkillSelectionError(
                "composition execution value schema coverage is invalid"
            )
        for value_schema in entry.value_schemas:
            try:
                value_body = value_schema.body()
            except (AttributeError, TypeError, ValueError) as exc:
                raise SkillSelectionError(
                    "composition execution value schema entry is invalid"
                ) from exc
            if (
                value_schema.action_id != entry.action_id
                or value_schema.canonical_action_id != entry.canonical_action_id
                or value_schema.action_version != entry.action_version
                or value_schema.source_manifest_sha256
                != entry.source_manifest_sha256
                or value_schema.validator_source_sha256
                != entry.value_validator_source_sha256
                or value_schema.kind != "EXPLICIT"
                or canonical_sha256(value_body)
                != value_schema.value_schema_sha256
                or value_body.get("value_schema_id")
                != value_schema.value_schema_id
                or value_body.get("kind") != "EXPLICIT"
                or value_schema.source_kind
                not in {"RESULT_PAYLOAD", "FACT_ID", "OUTPUT_OBJECT_REF"}
                or (
                    value_schema.source_kind == "RESULT_PAYLOAD"
                    and (
                        not isinstance(value_schema.json_pointer, str)
                        or not value_schema.json_pointer.startswith("/")
                    )
                )
                or (
                    value_schema.source_kind != "RESULT_PAYLOAD"
                    and value_schema.json_pointer is not None
                )
            ):
                raise SkillSelectionError(
                    "composition execution value schema entry is invalid"
                )

        model_action = model_actions[permission.action_id]
        if (
            model_action.argument_schema_sha256 != entry.argument_schema_sha256
            or model_action.result_schema_sha256 != entry.result_schema_sha256
        ):
            raise SkillSelectionError(
                "composition execution model and current schema mismatch"
            )
        compiled_actions.append(
            CapabilityAction(
                action_id=permission.action_id,
                version=permission.action_version,
                provider_component_id=model_action.provider_component_id,
                argument_schema_sha256=entry.argument_schema_sha256,
                result_schema_sha256=entry.result_schema_sha256,
                risk_class=permission.effective_risk,
                allowed_side_effects=permission.allowed_side_effects,
                idempotency_mode=model_action.idempotency_mode,
                max_runtime_ms=model_action.max_runtime_ms,
                max_output_bytes=model_action.max_output_bytes,
                max_tool_calls=model_action.max_tool_calls,
                available=model_action.available,
                unavailable_reason=model_action.unavailable_reason,
                model_visible=model_action.model_visible,
            )
        )

    draft = CapabilityManifest(
        manifest_id="omni-body-composition-execution-capabilities-v1",
        revision=checked_model.revision,
        generated_at_ms=(
            checked_model.generated_at_ms
            if generated_at_ms is None
            else generated_at_ms
        ),
        component_manifest_hash=checked_model.component_manifest_hash,
        actions=tuple(
            sorted(
                compiled_actions,
                key=lambda item: (item.action_id, item.version),
            )
        ),
        sha256="0" * 64,
    ).with_computed_sha256()
    if not draft.has_valid_sha256():  # defensive: never return unhashed authority
        raise SkillSelectionError(
            "composition execution capability manifest digest is invalid"
        )
    return draft


__all__ = [
    "LoadedModelCapabilityManifest",
    "SkillSelectionError",
    "compile_composition_execution_manifest",
    "load_model_capability_manifest",
]
