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
class ResolvedActionSchema:
    """Immutable schema authority for one executable manifest action."""

    action_id: str
    canonical_action_id: str
    action_version: str
    kind: str
    argument_schema_sha256: str
    validator_source_sha256: str
    source_manifest_sha256: str
    _body_json: bytes = field(repr=False)

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
        require_explicit: bool = False,
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
        if require_explicit and entry.kind != "EXPLICIT":
            raise ActionRegistryError("action schema is not explicit")
        return entry

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


def _schema_tuple(raw: Mapping[str, Any], *, action_id: str) -> tuple[bytes, str, str, str]:
    body = raw.get("argument_schema")
    digest = raw.get("argument_schema_sha256")
    kind = raw.get("argument_schema_kind")
    validator_digest = raw.get("argument_validator_source_sha256")
    if not isinstance(body, Mapping):
        raise ActionRegistryError(f"action schema body is invalid: {action_id}")
    if kind not in {"EXPLICIT", "OPAQUE"}:
        raise ActionRegistryError(f"action schema kind is invalid: {action_id}")
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ActionRegistryError(f"action schema hash is invalid: {action_id}")
    if (
        not isinstance(validator_digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", validator_digest) is None
    ):
        raise ActionRegistryError(f"action schema validator source hash is invalid: {action_id}")
    try:
        body_json = canonical_json_bytes(dict(body))
    except (TypeError, ValueError) as exc:
        raise ActionRegistryError(f"action schema body is not canonical JSON: {action_id}") from exc
    if canonical_sha256(dict(body)) != digest:
        raise ActionRegistryError(f"action schema hash mismatch: {action_id}")
    return body_json, digest, str(kind), validator_digest


def _compile_action_schema_catalog(
    manifest: Mapping[str, Any],
    registry: ActionRegistrySnapshot,
) -> ActionSchemaCatalog:
    capabilities = manifest.get("capabilities")
    if not isinstance(capabilities, Mapping):
        raise ActionRegistryError("action registry capabilities are missing")
    entries: list[ResolvedActionSchema] = []
    validator_hashes: set[str] = set()
    for permission in registry.permissions:
        action_id = permission.action_id
        raw = capabilities.get(action_id)
        if not isinstance(raw, Mapping):
            raise ActionRegistryError(f"action schema row is missing: {action_id}")
        _resolved, canonical_id, _risk = _resolve_capability(capabilities, action_id)
        canonical_raw = capabilities.get(canonical_id)
        if not isinstance(canonical_raw, Mapping):
            raise ActionRegistryError(f"action schema alias target is missing: {action_id}")
        body_json, digest, kind, validator_digest = _schema_tuple(
            raw, action_id=action_id
        )
        canonical_tuple = _schema_tuple(canonical_raw, action_id=canonical_id)
        if (body_json, digest, kind, validator_digest) != canonical_tuple:
            raise ActionRegistryError(f"action alias schema mismatch: {action_id}")
        body = json.loads(body_json.decode("utf-8", errors="strict"))
        if body.get("action") != canonical_id:
            raise ActionRegistryError(f"action schema canonical identity mismatch: {action_id}")
        validator_hashes.add(validator_digest)
        entries.append(
            ResolvedActionSchema(
                action_id=action_id,
                canonical_action_id=canonical_id,
                action_version=permission.action_version,
                kind=kind,
                argument_schema_sha256=digest,
                validator_source_sha256=validator_digest,
                source_manifest_sha256=registry.source_manifest_sha256,
                _body_json=body_json,
            )
        )
    if len(validator_hashes) != 1:
        raise ActionRegistryError("action schema validator source hash is inconsistent")
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
    "action_alias_map",
    "compile_action_authority",
    "compile_action_registry",
    "load_action_authority",
    "load_action_registry",
]
