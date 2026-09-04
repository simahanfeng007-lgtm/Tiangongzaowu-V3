"""Compile the complete Omni registry into fail-closed action permissions."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from contracts import (
    ActionPermission,
    ActionRegistrySnapshot,
    canonical_json_bytes,
    canonical_sha256,
)


class ActionRegistryError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ResolvedValueSchema:
    """Immutable value authority addressable by a catalog-owned digest."""

    action_id: str
    canonical_action_id: str
    action_version: str
    value_schema_id: str
    source_kind: str
    json_pointer: str | None
    kind: str
    value_schema_sha256: str
    validator_source_sha256: str
    source_manifest_sha256: str
    _body_json: bytes = field(repr=False)

    def body(self) -> dict[str, Any]:
        value = json.loads(
            self._body_json.decode("utf-8", errors="strict"),
            object_pairs_hook=_strict_pairs,
            parse_constant=_raise_constant,
        )
        if not isinstance(value, dict):
            raise ActionRegistryError("value schema body is corrupt")
        return value


@dataclass(frozen=True, slots=True)
class ResolvedActionSchema:
    """Immutable schema authority for one executable manifest action."""

    action_id: str
    canonical_action_id: str
    action_version: str
    kind: str
    argument_schema_sha256: str
    validator_source_sha256: str
    result_schema_kind: str
    result_schema_sha256: str
    result_validator_source_sha256: str
    value_schema_kind: str
    value_validator_source_sha256: str
    value_schemas: tuple[ResolvedValueSchema, ...]
    source_manifest_sha256: str
    _body_json: bytes = field(repr=False)
    _result_body_json: bytes = field(repr=False)

    def body(self) -> dict[str, Any]:
        """Return a detached body so callers cannot mutate the catalog."""

        value = json.loads(
            self._body_json.decode("utf-8", errors="strict"),
            object_pairs_hook=_strict_pairs,
            parse_constant=_raise_constant,
        )
        if not isinstance(value, dict):  # constructor enforces this invariant
            raise ActionRegistryError("action schema body is corrupt")
        return value

    def result_body(self) -> dict[str, Any]:
        """Return a detached result body so callers cannot mutate authority."""

        value = json.loads(
            self._result_body_json.decode("utf-8", errors="strict"),
            object_pairs_hook=_strict_pairs,
            parse_constant=_raise_constant,
        )
        if not isinstance(value, dict):
            raise ActionRegistryError("action result schema body is corrupt")
        return value

    def validate_exact(
        self,
        action: str,
        target: str,
        args: Mapping[str, Any],
        *,
        workspace: str | Path,
        available_actions: Iterable[str] = (),
        user_roots: Iterable[str | Path] = (),
    ) -> dict[str, Any]:
        """Run the existing pure validator and reject post-seal rewrites."""

        if action != self.action_id:
            raise ActionRegistryError("sealed action identity differs from schema authority")
        try:
            from omni_body_skill.tool_contracts import (
                argument_validator_source_sha256,
                validate_tool_request_exact,
            )

            if argument_validator_source_sha256() != self.validator_source_sha256:
                raise ActionRegistryError(
                    "action schema validator source hash mismatch"
                )

            return validate_tool_request_exact(
                action,
                target,
                args,
                canonical_action_id=self.canonical_action_id,
                workspace=workspace,
                available_actions=available_actions,
                user_roots=user_roots,
            )
        except ActionRegistryError:
            raise
        except (TypeError, ValueError) as exc:
            raise ActionRegistryError(str(exc)) from exc


@dataclass(frozen=True, slots=True)
class ActionSchemaCatalog:
    """Closed, immutable action-schema view compiled from the same manifest."""

    source_manifest_sha256: str
    entries: tuple[ResolvedActionSchema, ...]
    catalog_sha256: str

    def resolve(
        self,
        action_id: str,
        action_version: str,
        *,
        expected_sha256: str | None = None,
        expected_result_sha256: str | None = None,
        require_explicit: bool = False,
        require_result_explicit: bool = False,
    ) -> ResolvedActionSchema:
        if not self.has_valid_sha256():
            raise ActionRegistryError("action schema catalog hash is invalid")
        matches = tuple(item for item in self.entries if item.action_id == action_id)
        if len(matches) != 1:
            raise ActionRegistryError("action schema identity is absent or ambiguous")
        entry = matches[0]
        if entry.action_version != action_version:
            raise ActionRegistryError("action schema version mismatch")
        if expected_sha256 is not None and entry.argument_schema_sha256 != expected_sha256:
            raise ActionRegistryError("action schema hash mismatch")
        if (
            expected_result_sha256 is not None
            and entry.result_schema_sha256 != expected_result_sha256
        ):
            raise ActionRegistryError("action result schema hash mismatch")
        if require_explicit and entry.kind != "EXPLICIT":
            raise ActionRegistryError("action schema is not explicit")
        if require_result_explicit and entry.result_schema_kind != "EXPLICIT":
            raise ActionRegistryError("action result schema is not explicit")
        return entry

    def resolve_value_schema(
        self,
        action_id: str,
        action_version: str,
        value_schema_sha256: str,
        *,
        require_explicit: bool = True,
    ) -> ResolvedValueSchema:
        """Resolve a value digest only inside its sealed action authority."""

        entry = self.resolve(action_id, action_version)
        matches = tuple(
            item
            for item in entry.value_schemas
            if item.value_schema_sha256 == value_schema_sha256
        )
        if len(matches) != 1:
            raise ActionRegistryError("value schema identity is absent or ambiguous")
        resolved = matches[0]
        if require_explicit and resolved.kind != "EXPLICIT":
            raise ActionRegistryError("value schema is not explicit")
        return resolved

    def validate_value_exact(self, value_schema_sha256: str, value: Any) -> None:
        """Validate a value only when its digest is owned by this catalog."""

        if not self.has_valid_sha256():
            raise ActionRegistryError("action schema catalog hash is invalid")
        matches = tuple(
            value_schema
            for entry in self.entries
            for value_schema in entry.value_schemas
            if value_schema.value_schema_sha256 == value_schema_sha256
        )
        if not matches:
            raise ActionRegistryError("value schema identity is absent")
        body_keys = {
            (item._body_json, item.validator_source_sha256, item.kind)
            for item in matches
        }
        if len(body_keys) != 1 or any(item.kind != "EXPLICIT" for item in matches):
            raise ActionRegistryError("value schema identity is ambiguous or opaque")
        try:
            from omni_body_skill.tool_contracts import (
                validate_value_exact as validate_declared_value_exact,
                value_validator_source_sha256,
            )

            current_validator_sha256 = value_validator_source_sha256()
            if any(
                item.validator_source_sha256 != current_validator_sha256
                for item in matches
            ):
                raise ActionRegistryError(
                    "value schema validator source hash mismatch"
                )
            validate_declared_value_exact(value_schema_sha256, value)
        except ActionRegistryError:
            raise
        except (TypeError, ValueError) as exc:
            raise ActionRegistryError(str(exc)) from exc

    def validate_result_exact(
        self,
        action_id: str,
        action_version: str,
        result_payload: Any,
    ) -> None:
        """Validate a successful Omni result against sealed explicit authority."""

        entry = self.resolve(
            action_id,
            action_version,
            require_result_explicit=True,
        )
        try:
            from omni_body_skill.tool_contracts import (
                action_schema_descriptor,
                result_validator_source_sha256,
                validate_tool_result_exact,
            )

            if result_validator_source_sha256() != entry.result_validator_source_sha256:
                raise ActionRegistryError(
                    "action result validator source hash mismatch"
                )
            current = action_schema_descriptor(
                entry.canonical_action_id,
                enable_explicit_result=True,
            )
            if (
                current.get("result_schema_kind") != "EXPLICIT"
                or current.get("result_schema_sha256") != entry.result_schema_sha256
                or current.get("result_schema") != entry.result_body()
            ):
                raise ActionRegistryError("action result schema authority is stale")
            validate_tool_result_exact(
                action_id,
                result_payload,
                canonical_action_id=entry.canonical_action_id,
            )
        except ActionRegistryError:
            raise
        except (TypeError, ValueError) as exc:
            raise ActionRegistryError(str(exc)) from exc

    def payload(self) -> dict[str, Any]:
        return {
            "schema": "tiangong.action-schema-catalog.v1",
            "source_manifest_sha256": self.source_manifest_sha256,
            "entries": [
                {
                    "action_id": entry.action_id,
                    "canonical_action_id": entry.canonical_action_id,
                    "action_version": entry.action_version,
                    "kind": entry.kind,
                    "argument_schema": entry.body(),
                    "argument_schema_sha256": entry.argument_schema_sha256,
                    "validator_source_sha256": entry.validator_source_sha256,
                    "result_schema_kind": entry.result_schema_kind,
                    "result_schema": entry.result_body(),
                    "result_schema_sha256": entry.result_schema_sha256,
                    "result_validator_source_sha256": entry.result_validator_source_sha256,
                    "value_schema_kind": entry.value_schema_kind,
                    "value_validator_source_sha256": entry.value_validator_source_sha256,
                    "value_schemas": [
                        {
                            "value_schema_id": item.value_schema_id,
                            "source_kind": item.source_kind,
                            **(
                                {"json_pointer": item.json_pointer}
                                if item.json_pointer is not None
                                else {}
                            ),
                            "kind": item.kind,
                            "value_schema": item.body(),
                            "value_schema_sha256": item.value_schema_sha256,
                        }
                        for item in entry.value_schemas
                    ],
                }
                for entry in self.entries
            ],
        }

    def has_valid_sha256(self) -> bool:
        return self.catalog_sha256 == canonical_sha256(self.payload())


@dataclass(frozen=True, slots=True)
class LoadedActionAuthority:
    """Registry and schema views derived from one exact manifest payload."""

    registry: ActionRegistrySnapshot
    schema_catalog: ActionSchemaCatalog
    manifest_sha256: str
    manifest_source_hash: str
    _manifest_json: bytes = field(repr=False)

    @property
    def manifest(self) -> dict[str, Any]:
        value = json.loads(
            self._manifest_json.decode("utf-8", errors="strict"),
            object_pairs_hook=_strict_pairs,
            parse_constant=_raise_constant,
        )
        if not isinstance(value, dict):
            raise ActionRegistryError("action authority manifest is corrupt")
        return value


_RISK_ORDER = ("A0", "A1", "A2", "A3", "A4", "A5")
_EFFECT_FLOOR = {
    "read": "A0",
    "verify": "A0",
    "create": "A2",
    "write": "A2",
    "update": "A3",
    "execute": "A3",
}
_A4_ACTIONS = frozenset(
    {
        "blender.python.run",
        "core.archive.zip.extract",
        "core.code.python.run",
        "core.code.quality.run_tests",
        "core.code.shell.run",
        "core.filesystem.file.delete_to_trash",
        "core.rollback.rollback.apply",
        "file.delete_to_trash",
        "git.commit",
        "python.run",
        "quality.run_tests",
        "rollback.apply",
        "shell.run",
        "zip.extract",
    }
)
_SHELL_ACTIONS = frozenset(
    {
        "core.code.quality.run_tests",
        "core.code.shell.run",
        "quality.run_tests",
        "shell.run",
    }
)
_PYTHON_ACTIONS = frozenset(
    {"blender.python.run", "core.code.python.run", "python.run"}
)


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ActionRegistryError("action registry contains duplicate JSON keys")
        result[key] = value
    return result


def _raise_constant(_: str) -> None:
    raise ActionRegistryError("action registry contains a non-finite number")


def _risk_max(*values: str) -> str:
    if not values or any(value not in _RISK_ORDER for value in values):
        raise ActionRegistryError("action registry risk is invalid")
    return max(values, key=_RISK_ORDER.index)


def _side_effects(action_id: str, effect: str) -> tuple[str, ...]:
    values: set[str] = {"read"}
    if effect in {"create", "write", "update", "execute"}:
        values.add("local_write")
    folded = action_id.casefold()
    if "delete" in folded:
        values.add("destructive")
    if re.search(r"(?:^|\.)(?:send|publish|upload)(?:\.|$)", folded):
        values.add("external_write")
    return tuple(sorted(values))


def _resolve_capability(
    capabilities: Mapping[str, Any],
    action_id: str,
    *,
    trail: tuple[str, ...] = (),
) -> tuple[Mapping[str, Any], str, str]:
    """Resolve aliases without allowing an alias to lower canonical risk/effect."""
    if action_id in trail:
        raise ActionRegistryError("action registry alias cycle detected")
    raw = capabilities.get(action_id)
    if not isinstance(raw, Mapping) or raw.get("executable") is not True:
        raise ActionRegistryError("action registry alias target is missing or non-executable")
    alias_to = str(raw.get("alias_to") or "").strip()
    if not alias_to:
        return raw, action_id, str(raw.get("risk") or "")
    target, canonical_id, inherited_risk = _resolve_capability(
        capabilities, alias_to, trail=(*trail, action_id)
    )
    raw_risk = str(raw.get("risk") or "")
    if raw_risk not in _RISK_ORDER or inherited_risk not in _RISK_ORDER:
        raise ActionRegistryError("action registry alias risk is invalid")
    return target, canonical_id, _risk_max(raw_risk, inherited_risk)


def action_alias_map(manifest: Mapping[str, Any]) -> dict[str, str]:
    capabilities = manifest.get("capabilities")
    if not isinstance(capabilities, Mapping):
        raise ActionRegistryError("action registry capabilities are missing")
    result: dict[str, str] = {}
    for action_id in sorted(capabilities):
        raw = capabilities[action_id]
        if not isinstance(raw, Mapping) or raw.get("executable") is not True:
            continue
        _resolved, canonical_id, _risk = _resolve_capability(capabilities, action_id)
        result[action_id] = canonical_id
    return result


def compile_action_registry(
    manifest: Mapping[str, Any],
    *,
    generated_at_ms: int,
) -> ActionRegistrySnapshot:
    if generated_at_ms < 0:
        raise ValueError("action registry generation time is invalid")
    if manifest.get("schema") != "tiangong.v3.capability_manifest.v1":
        raise ActionRegistryError("action registry schema is unsupported")
    capabilities = manifest.get("capabilities")
    validation = manifest.get("validation")
    if not isinstance(capabilities, Mapping) or not isinstance(validation, Mapping):
        raise ActionRegistryError("action registry structure is incomplete")
    if validation.get("ok") is not True or validation.get("executable_without_route") != []:
        raise ActionRegistryError("action registry validation is not healthy")
    if not isinstance(manifest.get("source_hash"), str) or not re.fullmatch(
        r"[0-9a-f]{64}", str(manifest["source_hash"])
    ):
        raise ActionRegistryError("action registry source hash is invalid")

    source_manifest_sha256 = canonical_sha256(dict(manifest))
    permissions: list[ActionPermission] = []
    for action_id in sorted(capabilities):
        raw = capabilities[action_id]
        if not isinstance(raw, Mapping) or raw.get("executable") is not True:
            continue
        if raw.get("id") != action_id or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._:@-]{0,159}", action_id
        ):
            raise ActionRegistryError("executable action identity is invalid")
        resolved, canonical_id, registry_risk = _resolve_capability(capabilities, action_id)
        effect = str(resolved.get("effect") or "")
        if effect not in _EFFECT_FLOOR or registry_risk not in _RISK_ORDER:
            raise ActionRegistryError("executable action effect or risk is invalid")
        floor = "A4" if canonical_id in _A4_ACTIONS else _EFFECT_FLOOR[effect]
        effective_risk = _risk_max(registry_risk, floor)
        if effective_risk == "A5":
            raise ActionRegistryError("A5 registry action must not be executable")
        permission = ActionPermission(
            action_id=action_id,
            action_version="omni-registry-v1",
            registry_risk=registry_risk,
            effective_risk=effective_risk,
            effect=effect,
            handler=str(resolved.get("handler") or "")[:256],
            allowed_side_effects=_side_effects(canonical_id, effect),
            # Personal-super-assistant policy: A1-A4 actions may operate on
            # the host paths needed by the user's task.  Path impact is still
            # recomputed by the gateway, so credential/root destructive work
            # is elevated to A5 and rejected by the sovereign policy gate.
            # The workspace remains the default base for relative paths; it is
            # no longer an authority boundary for otherwise valid A1-A4 work.
            path_policy="object_grant_only",
            allow_absolute_paths=True,
            allow_shell=canonical_id in _SHELL_ACTIONS,
            allow_python=canonical_id in _PYTHON_ACTIONS,
            requires_confirmation=False,
            source_manifest_sha256=source_manifest_sha256,
            permission_sha256="0" * 64,
        ).with_computed_sha256()
        permissions.append(permission)

    declared_executable = manifest.get("executable")
    declared_total = manifest.get("total")
    if (
        isinstance(declared_executable, bool)
        or not isinstance(declared_executable, int)
        or declared_executable != len(permissions)
        or isinstance(declared_total, bool)
        or not isinstance(declared_total, int)
        or declared_total != len(capabilities)
    ):
        raise ActionRegistryError("action registry declared counts are stale")
    if not permissions:
        raise ActionRegistryError("action registry has no executable permissions")
    return ActionRegistrySnapshot(
        registry_id="omni-action-registry",
        revision=1,
        generated_at_ms=generated_at_ms,
        source_manifest_sha256=source_manifest_sha256,
        executable_count=len(permissions),
        permissions=tuple(permissions),
        registry_sha256="0" * 64,
    ).with_computed_sha256()


@dataclass(frozen=True, slots=True)
class _ParsedValueSchema:
    value_schema_id: str
    source_kind: str
    json_pointer: str | None
    kind: str
    digest: str
    body_json: bytes


@dataclass(frozen=True, slots=True)
class _ParsedSchemaDescriptor:
    argument_body_json: bytes
    argument_digest: str
    argument_kind: str
    argument_validator_digest: str
    result_body_json: bytes
    result_digest: str
    result_kind: str
    result_validator_digest: str
    value_schemas: tuple[_ParsedValueSchema, ...]
    value_kind: str
    value_validator_digest: str


def _require_schema_digest(value: Any, *, message: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ActionRegistryError(message)
    return value


def _schema_tuple(raw: Mapping[str, Any], *, action_id: str) -> _ParsedSchemaDescriptor:
    expected_keys = {
        "argument_schema",
        "argument_schema_sha256",
        "argument_schema_kind",
        "argument_validator_source_sha256",
        "result_schema",
        "result_schema_sha256",
        "result_schema_kind",
        "result_validator_source_sha256",
        "value_schemas",
        "value_schema_kind",
        "value_validator_source_sha256",
    }
    if not expected_keys.issubset(raw):
        raise ActionRegistryError(f"action schema descriptor is incomplete: {action_id}")

    body = raw.get("argument_schema")
    digest = _require_schema_digest(
        raw.get("argument_schema_sha256"),
        message=f"action schema hash is invalid: {action_id}",
    )
    kind = raw.get("argument_schema_kind")
    validator_digest = _require_schema_digest(
        raw.get("argument_validator_source_sha256"),
        message=f"action schema validator source hash is invalid: {action_id}",
    )
    if not isinstance(body, Mapping):
        raise ActionRegistryError(f"action schema body is invalid: {action_id}")
    if kind not in {"EXPLICIT", "OPAQUE"}:
        raise ActionRegistryError(f"action schema kind is invalid: {action_id}")
    try:
        body_json = canonical_json_bytes(dict(body))
    except (TypeError, ValueError) as exc:
        raise ActionRegistryError(f"action schema body is not canonical JSON: {action_id}") from exc
    if canonical_sha256(dict(body)) != digest:
        raise ActionRegistryError(f"action schema hash mismatch: {action_id}")

    result_body = raw.get("result_schema")
    result_digest = _require_schema_digest(
        raw.get("result_schema_sha256"),
        message=f"action result schema hash is invalid: {action_id}",
    )
    result_kind = raw.get("result_schema_kind")
    result_validator_digest = _require_schema_digest(
        raw.get("result_validator_source_sha256"),
        message=f"action result validator source hash is invalid: {action_id}",
    )
    if not isinstance(result_body, Mapping) or result_kind not in {"EXPLICIT", "OPAQUE"}:
        raise ActionRegistryError(f"action result schema body is invalid: {action_id}")
    expected_result_keys = {"schema", "action", "kind"}
    if result_kind == "EXPLICIT":
        expected_result_keys.add("root")
    if (
        set(result_body) != expected_result_keys
        or result_body.get("schema") != "tiangong.omni-action-result-schema.v1"
        or result_body.get("kind") != result_kind
        or not isinstance(result_body.get("action"), str)
        or (result_kind == "EXPLICIT" and not isinstance(result_body.get("root"), Mapping))
    ):
        raise ActionRegistryError(f"action result schema kind is invalid: {action_id}")
    try:
        result_body_json = canonical_json_bytes(dict(result_body))
    except (TypeError, ValueError) as exc:
        raise ActionRegistryError(
            f"action result schema body is not canonical JSON: {action_id}"
        ) from exc
    if canonical_sha256(dict(result_body)) != result_digest:
        raise ActionRegistryError(f"action result schema hash mismatch: {action_id}")

    raw_values = raw.get("value_schemas")
    value_kind = raw.get("value_schema_kind")
    value_validator_digest = _require_schema_digest(
        raw.get("value_validator_source_sha256"),
        message=f"action value validator source hash is invalid: {action_id}",
    )
    if not isinstance(raw_values, Mapping) or value_kind not in {"EXPLICIT", "OPAQUE"}:
        raise ActionRegistryError(f"action value schema authority is invalid: {action_id}")
    parsed_values: list[_ParsedValueSchema] = []
    for value_schema_id in sorted(raw_values):
        value_raw = raw_values[value_schema_id]
        if not isinstance(value_schema_id, str) or not re.fullmatch(
            r"[a-z][a-z0-9._-]{0,127}", value_schema_id
        ):
            raise ActionRegistryError(f"value schema identity is invalid: {action_id}")
        if not isinstance(value_raw, Mapping):
            raise ActionRegistryError(f"value schema descriptor is invalid: {action_id}")
        source_kind = value_raw.get("source_kind")
        expected_value_keys = {
            "value_schema_id",
            "source_kind",
            "value_schema",
            "value_schema_sha256",
            "value_schema_kind",
        }
        if source_kind == "RESULT_PAYLOAD":
            expected_value_keys.add("json_pointer")
        if (
            set(value_raw) != expected_value_keys
            or value_raw.get("value_schema_id") != value_schema_id
            or value_raw.get("value_schema_kind") != "EXPLICIT"
            or source_kind not in {"RESULT_PAYLOAD", "FACT_ID", "OUTPUT_OBJECT_REF"}
        ):
            raise ActionRegistryError(f"value schema descriptor is invalid: {action_id}")
        pointer = value_raw.get("json_pointer")
        if source_kind == "RESULT_PAYLOAD" and (
            not isinstance(pointer, str) or not pointer.startswith("/") or "//" in pointer
        ):
            raise ActionRegistryError(f"value schema selector is invalid: {action_id}")
        value_body = value_raw.get("value_schema")
        value_digest = _require_schema_digest(
            value_raw.get("value_schema_sha256"),
            message=f"value schema hash is invalid: {action_id}",
        )
        if not isinstance(value_body, Mapping) or (
            set(value_body) != {"schema", "value_schema_id", "kind", "root"}
            or value_body.get("schema") != "tiangong.omni-action-value-schema.v1"
            or value_body.get("value_schema_id") != value_schema_id
            or value_body.get("kind") != "EXPLICIT"
            or not isinstance(value_body.get("root"), Mapping)
        ):
            raise ActionRegistryError(f"value schema body is invalid: {action_id}")
        try:
            value_body_json = canonical_json_bytes(dict(value_body))
        except (TypeError, ValueError) as exc:
            raise ActionRegistryError(
                f"value schema body is not canonical JSON: {action_id}"
            ) from exc
        if canonical_sha256(dict(value_body)) != value_digest:
            raise ActionRegistryError(f"value schema hash mismatch: {action_id}")
        parsed_values.append(
            _ParsedValueSchema(
                value_schema_id=value_schema_id,
                source_kind=str(source_kind),
                json_pointer=str(pointer) if pointer is not None else None,
                kind="EXPLICIT",
                digest=value_digest,
                body_json=value_body_json,
            )
        )
    if (value_kind == "EXPLICIT") != bool(parsed_values):
        raise ActionRegistryError(f"action value schema coverage is invalid: {action_id}")
    if result_kind != value_kind:
        raise ActionRegistryError(f"action result/value schema authority differs: {action_id}")
    return _ParsedSchemaDescriptor(
        argument_body_json=body_json,
        argument_digest=digest,
        argument_kind=str(kind),
        argument_validator_digest=validator_digest,
        result_body_json=result_body_json,
        result_digest=result_digest,
        result_kind=str(result_kind),
        result_validator_digest=result_validator_digest,
        value_schemas=tuple(parsed_values),
        value_kind=str(value_kind),
        value_validator_digest=value_validator_digest,
    )


def _compile_action_schema_catalog(
    manifest: Mapping[str, Any],
    registry: ActionRegistrySnapshot,
) -> ActionSchemaCatalog:
    capabilities = manifest.get("capabilities")
    if not isinstance(capabilities, Mapping):
        raise ActionRegistryError("action registry capabilities are missing")
    entries: list[ResolvedActionSchema] = []
    argument_validator_hashes: set[str] = set()
    result_validator_hashes: set[str] = set()
    value_validator_hashes: set[str] = set()
    for permission in registry.permissions:
        action_id = permission.action_id
        raw = capabilities.get(action_id)
        if not isinstance(raw, Mapping):
            raise ActionRegistryError(f"action schema row is missing: {action_id}")
        _resolved, canonical_id, _risk = _resolve_capability(capabilities, action_id)
        canonical_raw = capabilities.get(canonical_id)
        if not isinstance(canonical_raw, Mapping):
            raise ActionRegistryError(f"action schema alias target is missing: {action_id}")
        descriptor = _schema_tuple(raw, action_id=action_id)
        canonical_tuple = _schema_tuple(canonical_raw, action_id=canonical_id)
        if descriptor != canonical_tuple:
            raise ActionRegistryError(f"action alias schema mismatch: {action_id}")
        body = json.loads(
            descriptor.argument_body_json.decode("utf-8", errors="strict")
        )
        if body.get("action") != canonical_id:
            raise ActionRegistryError(f"action schema canonical identity mismatch: {action_id}")
        result_body = json.loads(
            descriptor.result_body_json.decode("utf-8", errors="strict")
        )
        if result_body.get("action") != canonical_id:
            raise ActionRegistryError(
                f"action result schema canonical identity mismatch: {action_id}"
            )
        argument_validator_hashes.add(descriptor.argument_validator_digest)
        result_validator_hashes.add(descriptor.result_validator_digest)
        value_validator_hashes.add(descriptor.value_validator_digest)
        value_schemas = tuple(
            ResolvedValueSchema(
                action_id=action_id,
                canonical_action_id=canonical_id,
                action_version=permission.action_version,
                value_schema_id=item.value_schema_id,
                source_kind=item.source_kind,
                json_pointer=item.json_pointer,
                kind=item.kind,
                value_schema_sha256=item.digest,
                validator_source_sha256=descriptor.value_validator_digest,
                source_manifest_sha256=registry.source_manifest_sha256,
                _body_json=item.body_json,
            )
            for item in descriptor.value_schemas
        )
        entries.append(
            ResolvedActionSchema(
                action_id=action_id,
                canonical_action_id=canonical_id,
                action_version=permission.action_version,
                kind=descriptor.argument_kind,
                argument_schema_sha256=descriptor.argument_digest,
                validator_source_sha256=descriptor.argument_validator_digest,
                result_schema_kind=descriptor.result_kind,
                result_schema_sha256=descriptor.result_digest,
                result_validator_source_sha256=descriptor.result_validator_digest,
                value_schema_kind=descriptor.value_kind,
                value_validator_source_sha256=descriptor.value_validator_digest,
                value_schemas=value_schemas,
                source_manifest_sha256=registry.source_manifest_sha256,
                _body_json=descriptor.argument_body_json,
                _result_body_json=descriptor.result_body_json,
            )
        )
    if len(argument_validator_hashes) != 1:
        raise ActionRegistryError("action schema validator source hash is inconsistent")
    if len(result_validator_hashes) != 1:
        raise ActionRegistryError(
            "action result schema validator source hash is inconsistent"
        )
    if len(value_validator_hashes) != 1:
        raise ActionRegistryError(
            "action value schema validator source hash is inconsistent"
        )
    draft = ActionSchemaCatalog(
        source_manifest_sha256=registry.source_manifest_sha256,
        entries=tuple(entries),
        catalog_sha256="0" * 64,
    )
    return ActionSchemaCatalog(
        source_manifest_sha256=draft.source_manifest_sha256,
        entries=draft.entries,
        catalog_sha256=canonical_sha256(draft.payload()),
    )


def compile_action_authority(
    manifest: Mapping[str, Any],
    *,
    generated_at_ms: int,
) -> LoadedActionAuthority:
    """Compile permission and schema authority from the same manifest bytes."""

    capabilities = manifest.get("capabilities")
    validation = manifest.get("validation")
    if not isinstance(capabilities, Mapping) or not isinstance(validation, Mapping):
        raise ActionRegistryError("action authority manifest is incomplete")
    source_hash = manifest.get("source_hash")
    if (
        not isinstance(source_hash, str)
        or validation.get("source_hash") != source_hash
        or canonical_sha256(dict(capabilities)) != source_hash
    ):
        raise ActionRegistryError("action authority source hash is invalid")
    registry = compile_action_registry(manifest, generated_at_ms=generated_at_ms)
    schema_catalog = _compile_action_schema_catalog(manifest, registry)
    manifest_json = canonical_json_bytes(dict(manifest))
    return LoadedActionAuthority(
        registry=registry,
        schema_catalog=schema_catalog,
        manifest_sha256=registry.source_manifest_sha256,
        manifest_source_hash=source_hash,
        _manifest_json=manifest_json,
    )


def _load_manifest(path: Path) -> dict[str, Any]:
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ActionRegistryError("action registry path is missing or unsafe")
    raw = path.read_bytes()
    if not raw or len(raw) > 16 * 1024 * 1024:
        raise ActionRegistryError("action registry size is invalid")
    try:
        manifest = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_strict_pairs,
            parse_constant=_raise_constant,
        )
    except ActionRegistryError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ActionRegistryError("action registry JSON is invalid") from exc
    if not isinstance(manifest, dict):
        raise ActionRegistryError("action registry root must be an object")
    # Reject alternate byte encodings of the same object.  The checked-in
    # manifest is pretty-printed, so canonical bytes are used only as a stable
    # semantic round trip rather than a byte equality requirement.
    canonical_json_bytes(manifest)
    return manifest


def load_action_authority(path: Path, *, generated_at_ms: int) -> LoadedActionAuthority:
    return compile_action_authority(
        _load_manifest(path),
        generated_at_ms=generated_at_ms,
    )


def load_action_registry(path: Path, *, generated_at_ms: int) -> ActionRegistrySnapshot:
    """Compatibility facade returning only the existing permission snapshot."""

    return load_action_authority(path, generated_at_ms=generated_at_ms).registry


__all__ = [
    "ActionRegistryError",
    "ActionSchemaCatalog",
    "LoadedActionAuthority",
    "ResolvedActionSchema",
    "ResolvedValueSchema",
    "action_alias_map",
    "compile_action_authority",
    "compile_action_registry",
    "load_action_authority",
    "load_action_registry",
]
