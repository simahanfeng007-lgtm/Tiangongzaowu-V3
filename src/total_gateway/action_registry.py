"""Compile the complete Omni registry into fail-closed action permissions."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Mapping

from contracts import (
    ActionPermission,
    ActionRegistrySnapshot,
    canonical_json_bytes,
    canonical_sha256,
)


class ActionRegistryError(ValueError):
    pass


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
    if folded.startswith("mobile.") and effect == "execute":
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


def _mobile_overlay(manifest: dict[str, Any]) -> dict[str, Any]:
    if os.environ.get("TIANGONG_MOBILE_LINK", "0") != "1":
        return manifest
    try:
        from .mobile_capabilities import augment_capability_manifest, capability_manifest_entries
    except ImportError:
        from mobile_capabilities import augment_capability_manifest, capability_manifest_entries
    overlay_hash = canonical_sha256(
        {
            "base_source_hash": str(manifest.get("source_hash") or ""),
            "mobile_capabilities": capability_manifest_entries(),
            "schema": "tiangong.mobile.capability.overlay.v1",
        }
    )
    try:
        return augment_capability_manifest(manifest, source_hash=overlay_hash)
    except (TypeError, ValueError) as exc:
        raise ActionRegistryError(str(exc)) from exc


def load_action_registry(path: Path, *, generated_at_ms: int) -> ActionRegistrySnapshot:
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
    canonical_json_bytes(manifest)
    manifest = _mobile_overlay(manifest)
    return compile_action_registry(manifest, generated_at_ms=generated_at_ms)


__all__ = [
    "ActionRegistryError",
    "action_alias_map",
    "compile_action_registry",
    "load_action_registry",
]
